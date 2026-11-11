from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from runcoach.config import ConfigNode, load_scheduling_config
from runcoach.domain.models import Commitment, PlanInput, PlannedSession
from runcoach.engine.events import expand_commitments
from runcoach.engine.progression import weekly_targets
from runcoach.engine.safety import check_plan
from runcoach.engine.scheduler import (
    Busy,
    DroppedSession,
    FixedInstance,
    compose_week,
    estimate_combined_leg_load,
    schedule_week,
)


class InfeasibleGoal(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PlanGenerationResult:
    sessions: list[PlannedSession]
    dropped: list[DroppedSession]
    warnings: list[str]


def _required_weeks_for_distance(distance_km: float) -> int:
    if distance_km <= 5.0:
        return 6
    if distance_km <= 10.0:
        return 8
    if distance_km <= 21.1:
        return 10
    return 12


def _generate_race_weeks(
    inp: PlanInput,
    today: date,
    cfg: object,
) -> tuple[list[list[PlannedSession]], int]:
    if inp.runs_per_week < 2 or inp.runs_per_week > 6:
        raise InfeasibleGoal("Run count is outside the supported range")

    min_weeks = _required_weeks_for_distance(inp.distance_km)
    weeks_available = max(1, (inp.goal_date - today).days // 7)
    if weeks_available < min_weeks:
        suggested_date = today + timedelta(weeks=min_weeks)
        raise InfeasibleGoal(
            f"Not enough weeks to build a safe plan for {inp.distance_km:.1f} km; "
            f"move the goal to {suggested_date.isoformat()} or later."
        )

    progression = getattr(cfg, "progression", None) if cfg is not None else None
    max_weekly_increase = float(getattr(progression, "max_weekly_increase", 0.10))
    projected = inp.current_weekly_km
    for _ in range(weeks_available):
        projected *= 1.0 + max_weekly_increase
    if projected < inp.distance_km * 0.6:
        suggested_date = today + timedelta(weeks=min_weeks)
        raise InfeasibleGoal(
            f"Reaching the target volume from {inp.current_weekly_km} km would need growth "
            f"above the cap; choose a later date such as {suggested_date.isoformat()}."
        )

    target_km_by_week = weekly_targets(inp.current_weekly_km, weeks_available, "half", cfg)
    weekly_sessions: list[list[PlannedSession]] = []
    for week_index, weekly_target in enumerate(target_km_by_week):
        session_date = today + timedelta(weeks=week_index)
        sessions: list[PlannedSession] = []
        for run_index in range(inp.runs_per_week):
            if run_index == 0:
                kind = "long"
                title = "Long run"
                duration_min = 60
                distance_m = int(weekly_target * 1000 * 0.28)
                rpe = 4.0
            elif run_index == 1:
                kind = "tempo"
                title = "Tempo"
                duration_min = 35
                distance_m = int(weekly_target * 1000 * 0.18)
                rpe = 6.0
            else:
                kind = "easy"
                title = "Easy run"
                duration_min = 30 + run_index * 5
                distance_m = int(weekly_target * 1000 * 0.12)
                rpe = 3.0
            sessions.append(
                PlannedSession(
                    date=session_date + timedelta(days=run_index * 2),
                    sport="run",
                    kind=kind,
                    title=title,
                    steps=[],
                    duration_min=duration_min,
                    distance_m=max(2000, distance_m),
                    rpe=rpe,
                    leg_load=float(duration_min) * rpe,
                    finger_load=0.0,
                    reason="Generated from the race plan",
                )
            )
        weekly_sessions.append(sessions)

    return weekly_sessions, weeks_available


def generate_race_plan_result(
    inp: PlanInput,
    today: date,
    cfg: ConfigNode,
    *,
    fixed: list[FixedInstance] | None = None,
    commitments: list[Commitment] | None = None,
    busy: list[Busy] | None = None,
    prior_tail: list[PlannedSession] | None = None,
    timezone_name: str = "Europe/London",
) -> PlanGenerationResult:
    """Generate, load-compose, and schedule a race plan with diagnostics."""
    typed_cfg = load_scheduling_config(cfg)
    weekly_sessions, weeks_available = _generate_race_weeks(inp, today, cfg)
    fixed_instances = list(fixed or [])
    busy_events = busy or []
    tail = list(prior_tail or [])
    output: list[PlannedSession] = []
    dropped: list[DroppedSession] = []
    warnings: list[str] = []
    relaxed_weeks: set[date] = set()

    first_monday = today - timedelta(days=today.weekday())
    final_day = first_monday + timedelta(weeks=weeks_available, days=-1)
    for occurrence in expand_commitments(commitments or [], today, final_day):
        if occurrence.kind not in {
            "fixed",
            "climb_power",
            "climb_volume",
            "climb_technique",
        }:
            continue
        if occurrence.sport == "climb" and occurrence.kind == "fixed":
            warnings.append(
                f"Skipped climbing commitment {occurrence.title}: choose a climbing kind."
            )
            continue
        if occurrence.start_time is None or occurrence.duration_min is None:
            warnings.append(
                f"Skipped fixed commitment {occurrence.title}: start time and "
                "duration are required."
            )
            continue
        fixed_instances.append(
            FixedInstance(
                date=occurrence.date,
                title=occurrence.title,
                sport=occurrence.sport or "other",
                start_time=occurrence.start_time,
                duration_min=occurrence.duration_min,
                rpe=occurrence.rpe,
                kind=occurrence.kind,
            )
        )

    first_week_fixed = [
        instance
        for instance in fixed_instances
        if max(today, first_monday) <= instance.date < first_monday + timedelta(days=7)
    ]
    first_week_load = estimate_combined_leg_load(
        first_monday,
        weekly_sessions[0],
        first_week_fixed,
        inp.climbing_target_per_week,
        typed_cfg,
    )
    previous_week_load = first_week_load / (1.0 + typed_cfg.progression.max_weekly_increase)
    for week_index, sessions in enumerate(weekly_sessions):
        week_start = first_monday + timedelta(weeks=week_index)
        week_end = week_start + timedelta(days=7)
        fixed_this_week = [
            instance
            for instance in fixed_instances
            if max(today, week_start) <= instance.date < week_end
        ]
        composition = compose_week(
            week_start,
            sessions,
            fixed_this_week,
            previous_week_load,
            inp.climbing_target_per_week,
            (week_index + 1) % typed_cfg.progression.cutback_every_n_weeks == 0,
            typed_cfg,
        )
        schedule = schedule_week(
            week_start,
            composition.sessions,
            0,
            fixed_this_week,
            busy_events,
            tail,
            typed_cfg,
            timezone_name=timezone_name,
            not_before=today if week_index == 0 else None,
        )
        output.extend(
            placement.session for placement in schedule.placed if placement.session is not None
        )
        dropped.extend(schedule.dropped)
        warnings.extend(composition.warnings)
        warnings.extend(schedule.warnings)
        if schedule.relaxed:
            relaxed_weeks.add(week_start)
        tail = [placement.session for placement in schedule.placed if placement.session is not None]
        previous_week_load = composition.actual_leg_load

    plan_end = first_monday + timedelta(weeks=weeks_available)
    plan_fixed = [
        instance
        for instance in fixed_instances
        if max(today, first_monday) <= instance.date < plan_end
    ]
    warnings.extend(
        f"Safety check ({violation.kind}): {violation.message}"
        for violation in check_plan(
            output,
            typed_cfg,
            fixed=plan_fixed,
            busy=busy_events,
            timezone_name=timezone_name,
            relaxed_weeks=relaxed_weeks,
        )
    )
    return PlanGenerationResult(sessions=output, dropped=dropped, warnings=warnings)


def generate_race_plan(
    inp: PlanInput,
    today: date,
    cfg: object,
    *,
    commitments: list[Commitment] | None = None,
    busy: list[Busy] | None = None,
    fixed: list[FixedInstance] | None = None,
    prior_tail: list[PlannedSession] | None = None,
    timezone_name: str = "Europe/London",
) -> list[PlannedSession]:
    if cfg is None:
        if commitments or busy or fixed or prior_tail:
            raise ValueError("cfg is required when scheduling constraints are supplied")
        weekly_sessions, _ = _generate_race_weeks(inp, today, cfg)
        return [session for sessions in weekly_sessions for session in sessions]
    if not isinstance(cfg, ConfigNode):
        raise TypeError("cfg must be a ConfigNode or None")
    return generate_race_plan_result(
        inp,
        today,
        cfg,
        fixed=fixed,
        commitments=commitments,
        busy=busy,
        prior_tail=prior_tail,
        timezone_name=timezone_name,
    ).sessions
