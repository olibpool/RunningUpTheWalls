from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import ceil, floor
from typing import Literal

from runcoach.config import SchedulingConfig
from runcoach.domain.models import PlannedSession, Step
from runcoach.engine.load import session_load
from runcoach.engine.vdot import race_time_s
from runcoach.engine.zones import ZoneRange, Zones

RUN_KINDS = ("easy", "recovery", "long", "tempo", "intervals")


@dataclass(frozen=True)
class WeekContext:
    date: date
    minutes: int  # target total duration of the session
    progress: float = 0.0  # 0 at the start of build, 1 at the end of peak
    strides: bool = False  # append strides to an easy run
    marathon_finish: bool = False  # end a long run at marathon pace


def _lerp(bounds: tuple[float, float], progress: float) -> float:
    low, high = bounds
    return low + (high - low) * min(1.0, max(0.0, progress))


def _paced(
    kind: Literal["warmup", "work", "recover", "cooldown", "steady"],
    seconds: int,
    zone: ZoneRange,
    rpe: float | None = None,
) -> Step:
    return Step(
        kind=kind,
        duration_s=seconds,
        pace_min_s_per_km=zone.min_s_per_km,
        pace_max_s_per_km=zone.max_s_per_km,
        rpe=rpe,
    )


def _seconds(steps: list[Step]) -> int:
    total = 0
    for step in steps:
        if step.kind == "repeat":
            total += (step.repeats or 0) * _seconds(step.children)
        else:
            total += step.duration_s or 0
    return total


def _metres(steps: list[Step]) -> float:
    total = 0.0
    for step in steps:
        if step.kind == "repeat":
            total += (step.repeats or 0) * _metres(step.children)
        elif step.duration_s and step.pace_min_s_per_km and step.pace_max_s_per_km:
            mid_pace = (step.pace_min_s_per_km + step.pace_max_s_per_km) / 2
            total += step.duration_s / mid_pace * 1000
    return total


def quality_minutes(kind: str, progress: float, cfg: SchedulingConfig) -> int:
    """Total minutes of a tempo or interval session at a given block progress."""
    t = cfg.templates
    if kind == "tempo":
        work = round(_lerp(t.tempo_work_min, progress))
    elif kind == "intervals":
        work = (
            2
            * round(_lerp(t.interval_rep_min, progress))
            * round(_lerp(t.interval_repeats, progress))
        )
    else:
        raise ValueError(f"{kind} is not a quality session")
    return t.warmup_min + work + t.cooldown_min


def build_session(
    kind: str, ctx: WeekContext, zones: Zones, cfg: SchedulingConfig
) -> PlannedSession:
    """Build a run session as structured steps with pace targets."""
    t = cfg.templates
    warmup = _paced("warmup", t.warmup_min * 60, zones.easy)
    cooldown = _paced("cooldown", t.cooldown_min * 60, zones.easy)
    if kind == "long" and ctx.marathon_finish:
        finish = min(t.long_mp_finish_max_min, max(1, round(ctx.minutes * t.long_mp_finish_share)))
        steps = [
            _paced("steady", (ctx.minutes - finish) * 60, zones.easy),
            _paced("work", finish * 60, zones.marathon),
        ]
        title = "Long run, marathon-pace finish"
        rpe = cfg.rpe.long_mp
    elif kind in {"easy", "recovery", "long"}:
        steps = [_paced("steady", ctx.minutes * 60, zones.easy)]
        title = {"easy": "Easy run", "recovery": "Recovery run", "long": "Long run"}[kind]
        if ctx.strides and kind == "easy":
            stride = _paced("work", t.stride_s, zones.repetition, cfg.rpe.strides)
            jog = Step(kind="recover", duration_s=t.stride_recovery_s)
            steps.append(Step(kind="repeat", repeats=t.strides_repeats, children=[stride, jog]))
            title = "Easy run + strides"
        rpe = getattr(cfg.rpe, kind)
    elif kind == "tempo":
        low, high = t.tempo_work_min
        work = min(high, max(low, ctx.minutes - t.warmup_min - t.cooldown_min))
        if work <= t.tempo_continuous_max_min:
            main = _paced("work", work * 60, zones.threshold)
        else:
            # Long threshold work is run as cruise intervals: the same stimulus, less strain.
            reps = ceil(work / t.cruise_rep_max_min)
            rep = round(work / reps)
            jog = Step(kind="recover", duration_s=rep * t.cruise_recovery_s_per_5min // 5)
            main = Step(
                kind="repeat",
                repeats=reps,
                children=[_paced("work", rep * 60, zones.threshold), jog],
            )
        steps = [warmup, main, cooldown]
        title = "Tempo"
        rpe = cfg.rpe.tempo
    elif kind == "intervals":
        rep = round(_lerp(t.interval_rep_min, ctx.progress))
        low, high = t.interval_repeats
        available = (ctx.minutes - t.warmup_min - t.cooldown_min) / 2  # reps and recoveries
        reps = min(high, max(low, round(available / rep)))
        if reps * rep > available:  # too little time for the minimum reps: shorten them instead
            rep = max(t.interval_rep_min[0], int(available // reps))
        children = [
            _paced("work", rep * 60, zones.interval),
            _paced("recover", rep * 60, zones.easy),
        ]
        steps = [warmup, Step(kind="repeat", repeats=reps, children=children), cooldown]
        title = "Intervals"
        rpe = cfg.rpe.intervals
    else:
        raise ValueError(f"Unsupported run session kind: {kind}")

    minutes = round(_seconds(steps) / 60)
    return PlannedSession(
        date=ctx.date,
        sport="run",
        kind=kind,
        title=title,
        steps=steps,
        duration_min=minutes,
        distance_m=round(_metres(steps)),
        rpe=rpe,
        leg_load=session_load("run", kind, rpe, minutes, cfg.load_factors).leg,
    )


def race_session(
    day: date, distance_m: int, label: str, vdot: float, cfg: SchedulingConfig
) -> PlannedSession:
    """Race day: a warm-up jog, then the distance at the pace the current VDOT predicts."""
    seconds = round(race_time_s(distance_m, vdot))
    pace = seconds / distance_m * 1000
    steps = [
        Step(kind="warmup", duration_s=cfg.race.warmup_min * 60, rpe=cfg.rpe.easy),
        Step(
            kind="work",
            distance_m=distance_m,
            pace_min_s_per_km=pace,
            pace_max_s_per_km=pace,
            rpe=cfg.rpe.race,
        ),
    ]
    minutes = cfg.race.warmup_min + round(seconds / 60)
    target = f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return PlannedSession(
        date=day,
        start_time=cfg.race.start_time,
        sport="run",
        kind="race",
        title=f"Race: {label}",
        steps=steps,
        duration_min=minutes,
        distance_m=distance_m,
        rpe=cfg.rpe.race,
        leg_load=session_load("run", "race", cfg.rpe.race, minutes, cfg.load_factors).leg,
        reason=f"Race day. Your VDOT {vdot:g} predicts {target}; start a little slower.",
    )


def _scaled_step(step: Step, factor: float) -> Step:
    """Trim like a coach: running shortens in whole minutes (warm-ups to no less than 5) and
    rep sessions and strides lose whole reps; distance efforts (races, trials) stay."""
    if step.kind == "repeat":
        return step.model_copy(update={"repeats": max(1, round((step.repeats or 1) * factor))})
    if step.duration_s is None:
        return step
    least = 5 if step.kind in {"warmup", "cooldown"} else 1  # minutes; a warm-up stays useful
    # Rounding down keeps a trimmed session within the load it was trimmed to.
    scaled = step.duration_s * factor / 60
    minutes = max(min(least, step.duration_s // 60), floor(scaled) if factor < 1 else round(scaled))
    return step.model_copy(update={"duration_s": minutes * 60})


def scale_session[S: PlannedSession](session: S, factor: float) -> S:
    """Scale a session's volume by about factor; kind and paces unchanged.

    Steps are trimmed in whole minutes and whole reps, so the factor achieved is approximate;
    duration, distance and load follow the steps actually kept."""
    if not session.steps or factor == 1.0:
        return session.model_copy(
            update={
                "duration_min": max(1, round(session.duration_min * factor))
                if session.duration_min
                else None,
                "distance_m": max(1, round(session.distance_m * factor))
                if session.distance_m
                else None,
                "leg_load": session.leg_load * factor,
                "finger_load": session.finger_load * factor,
            }
        )
    steps = [_scaled_step(step, factor) for step in session.steps]
    before, after = _seconds(session.steps), _seconds(steps)
    ratio = after / before if before else factor
    metres = _metres(steps)
    distance = (
        round(metres)
        if metres
        else (max(1, round(session.distance_m * ratio)) if session.distance_m else None)
    )
    return session.model_copy(
        update={
            "steps": steps,
            "duration_min": max(1, round(after / 60)),
            "distance_m": distance,
            "leg_load": session.leg_load * ratio,
            "finger_load": session.finger_load * ratio,
        }
    )
