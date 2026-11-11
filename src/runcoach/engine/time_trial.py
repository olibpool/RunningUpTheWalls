from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from math import isfinite

from runcoach.config import ConfigNode, SchedulingConfig, load_scheduling_config
from runcoach.domain.models import Athlete, PlannedSession, Step
from runcoach.engine.load import session_load
from runcoach.engine.vdot import vdot_from_race


def vdot_from_time_trial(distance_m: float, time_s: float) -> float:
    if not isfinite(distance_m) or distance_m <= 0:
        raise ValueError("distance_m must be a positive finite number")
    if not isfinite(time_s) or time_s <= 0:
        raise ValueError("time_s must be a positive finite number")
    return vdot_from_race(distance_m, time_s)


def time_trial_session(
    day: date,
    cfg: ConfigNode | SchedulingConfig,
) -> PlannedSession:
    typed_cfg = cfg if isinstance(cfg, SchedulingConfig) else load_scheduling_config(cfg)
    trial = typed_cfg.time_trial
    distance_m = typed_cfg.continuous.retest_distance_m
    duration_min = trial.warmup_min + trial.estimated_5k_duration_min + trial.cooldown_min
    segment_loads = (
        session_load("run", "easy", trial.warmup_rpe, trial.warmup_min, typed_cfg.load_factors),
        session_load(
            "run",
            "time_trial",
            trial.work_rpe,
            trial.estimated_5k_duration_min,
            typed_cfg.load_factors,
        ),
        session_load("run", "easy", trial.cooldown_rpe, trial.cooldown_min, typed_cfg.load_factors),
    )
    return PlannedSession(
        date=day,
        sport="run",
        kind="time_trial",
        title=f"{distance_m // 1000} km time trial",
        steps=[
            Step(kind="warmup", duration_s=trial.warmup_min * 60),
            Step(kind="work", distance_m=distance_m, rpe=trial.work_rpe),
            Step(kind="cooldown", duration_s=trial.cooldown_min * 60),
        ],
        duration_min=duration_min,
        distance_m=distance_m,
        rpe=trial.work_rpe,
        leg_load=sum(item.leg for item in segment_loads),
        finger_load=sum(item.finger for item in segment_loads),
        reason="Scheduled retest; confirm the result before updating VDOT.",
    )


def apply_time_trial_result(
    athlete: Athlete,
    distance_m: float,
    time_s: float,
    *,
    confirmed: bool,
    confirmed_at: str,
) -> Athlete:
    if not confirmed:
        return athlete
    try:
        timestamp = datetime.fromisoformat(confirmed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("confirmed_at must be an ISO 8601 UTC timestamp") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError("confirmed_at must be an ISO 8601 UTC timestamp")
    return athlete.model_copy(
        update={
            "vdot": vdot_from_time_trial(distance_m, time_s),
            "vdot_source": "time_trial",
            "vdot_updated_at": timestamp.astimezone(timezone.utc).isoformat(),
        }
    )
