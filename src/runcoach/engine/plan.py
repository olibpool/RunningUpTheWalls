from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

from runcoach.config import ConfigNode, SchedulingConfig, load_scheduling_config
from runcoach.domain.models import Commitment, PlanInput, PlannedSession
from runcoach.engine.events import DayConstraint, expand_commitments
from runcoach.engine.progression import is_cutback_week, split_blocks, weekly_targets
from runcoach.engine.safety import Violation, check_plan
from runcoach.engine.scheduler import (
    Busy,
    DroppedSession,
    FixedInstance,
    compose_week,
    estimate_combined_leg_load,
    schedule_week,
)
from runcoach.engine.templates import WeekContext, build_session, quality_minutes
from runcoach.engine.zones import Zones, zones_from_vdot

ROLES: dict[int, list[str]] = {
    2: ["quality", "long"],
    3: ["easy", "quality", "long"],
    4: ["easy", "quality", "easy", "long"],
    5: ["easy", "quality", "easy", "quality", "long"],
    6: ["easy", "quality", "easy", "quality", "recovery", "long"],
}
_QUALITY_ROTATION = ("tempo", "intervals")


class InfeasibleGoal(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PlanGenerationResult:
    sessions: list[PlannedSession]
    dropped: list[DroppedSession]
    warnings: list[str]
    violations: list[Violation]


@dataclass(frozen=True)
class _Week:
    start: date
    label: str  # base | build | peak | taper
    progress: float
    target_km: float
    cutback: bool
    race_week: bool


def _start_monday(today: date) -> date:
    return today + timedelta(days=(7 - today.weekday()) % 7)


def _week_count(start: date, goal: date) -> int:
    return (goal - start).days // 7 + 1 if goal >= start else 0


def _peak_km(inp: PlanInput, n_weeks: int, cfg: SchedulingConfig) -> float:
    return max(weekly_targets(inp.current_weekly_km, n_weeks, inp.distance, cfg), default=0.0)


def _check_feasible(inp: PlanInput, start: date, n_weeks: int, cfg: SchedulingConfig) -> None:
    min_weeks = cfg.race_min_weeks[inp.distance]
    if n_weeks < min_weeks:
        suggested = start + timedelta(weeks=min_weeks, days=-1)
        raise InfeasibleGoal(
            f"A {inp.distance} plan needs at least {min_weeks} weeks but only {n_weeks} remain; "
            f"move the goal to {suggested.isoformat()} or later."
        )
    needed = cfg.min_peak_weekly_km[inp.distance]
    if _peak_km(inp, n_weeks, cfg) >= needed:
        return
    weeks = n_weeks
    # shortcut: searches at most two years ahead; beyond that no date is suggested.
    while weeks < 104 and _peak_km(inp, weeks, cfg) < needed:
        weeks += 1
    later = (
        f"choose a later date such as {(start + timedelta(weeks=weeks, days=-1)).isoformat()}"
        if weeks < 104
        else "build up regular running first"
    )
    raise InfeasibleGoal(
        f"Reaching {needed:g} km a week from {inp.current_weekly_km:g} km would need growth "
        f"above the {cfg.progression.max_weekly_increase:.0%} weekly cap; {later}."
    )


def _weeks(inp: PlanInput, start: date, n_weeks: int, cfg: SchedulingConfig) -> list[_Week]:
    targets = weekly_targets(inp.current_weekly_km, n_weeks, inp.distance, cfg)
    labels = [b.label for b in split_blocks(n_weeks, inp.distance, cfg) for _ in range(b.weeks)]
    progressing = [i for i, label in enumerate(labels) if label in {"build", "peak"}]
    weeks: list[_Week] = []
    for index, (label, target) in enumerate(zip(labels, targets)):
        progress = (
            (index - progressing[0]) / max(1, len(progressing) - 1) if index in progressing else 0.0
        )
        weeks.append(
            _Week(
                start=start + timedelta(weeks=index),
                label=label,
                progress=progress,
                target_km=target,
                cutback=label != "taper" and is_cutback_week(index, cfg),
                race_week=index == n_weeks - 1,
            )
        )
    return weeks


def _week_sessions(
    week: _Week,
    index: int,
    roles: list[str],
    zones: Zones,
    cfg: SchedulingConfig,
) -> list[PlannedSession]:
    """Turn a weekly km target into templated sessions for the week's roles."""
    t = cfg.templates
    easy_pace = (zones.easy.min_s_per_km + zones.easy.max_s_per_km) / 2  # s/km
    if week.race_week:
        roles = [role for role in roles if role != "long"]  # no long run in race week

    quality: list[PlannedSession] = []
    quality_cap = week.target_km * easy_pace / 60 * t.max_quality_share
    for slot, role in enumerate(role for role in roles if role == "quality"):
        kind = _QUALITY_ROTATION[(index + slot) % len(_QUALITY_ROTATION)]
        minutes = min(quality_minutes(kind, week.progress, cfg), round(quality_cap))
        if minutes < quality_minutes(kind, 0.0, cfg):
            # Too little volume for even the shortest quality session; run it easy instead.
            roles = [*roles, "easy"]
            continue
        quality.append(
            build_session(kind, WeekContext(week.start, minutes, week.progress), zones, cfg)
        )

    share_low, share_high = t.long_share
    share = share_low + (share_high - share_low) * week.progress
    long_target = min(
        round(week.target_km * share * easy_pace / 60), cfg.progression.max_long_run_minutes
    )
    long_minutes = long_target if "long" in roles else 0
    long_km = long_minutes * 60 / easy_pace

    weights = {"easy": 1.0, "recovery": t.recovery_share_of_easy}
    easy_roles = [role for role in roles if role in weights]
    quality_km = sum((s.distance_m or 0) for s in quality) / 1000
    remaining_km = max(0.0, week.target_km - quality_km - long_km)
    unit_km = remaining_km / sum(weights[role] for role in easy_roles) if easy_roles else 0.0
    easy: list[PlannedSession] = []
    strides_due = week.label in {"build", "peak"}
    for role in easy_roles:
        minutes = max(t.min_easy_minutes, round(unit_km * weights[role] * easy_pace / 60))
        minutes = max(1, min(minutes, long_target))  # an easy run never outgrows the long run
        strides = strides_due and role == "easy"
        strides_due = strides_due and not strides
        context = WeekContext(week.start, minutes, week.progress, strides=strides)
        easy.append(build_session(role, context, zones, cfg))

    sessions = quality + easy
    if long_minutes:
        if len(sessions) >= 2:  # 2-run weeks are exempt from the share rule
            share_cap = cfg.progression.max_long_run_share
            others = sum(s.duration_min or 0 for s in sessions)
            # Keep the long run within its share of weekly run time, even when volume is low.
            long_minutes = max(1, min(long_minutes, int(share_cap / (1 - share_cap) * others)))
        sessions.append(build_session("long", WeekContext(week.start, long_minutes), zones, cfg))

    note = f"{week.label.title()} block"
    if week.cutback:
        note += ", cut-back week"
    if week.race_week:
        note += ", race week"
    return [s.model_copy(update={"reason": note}) for s in sessions]


def _fixed_instances(
    commitments: list[Commitment], start: date, end: date
) -> tuple[list[FixedInstance], list[str]]:
    fixed: list[FixedInstance] = []
    warnings: list[str] = []
    for occurrence in expand_commitments(commitments, start, end):
        if occurrence.kind != "fixed":
            continue
        if occurrence.sport == "climb":
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
        fixed.append(
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
    return fixed, warnings


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
    """Generate, load-compose and schedule a race plan; violations are returned, not raised."""
    typed_cfg = load_scheduling_config(cfg)
    start = _start_monday(today)
    n_weeks = _week_count(start, inp.goal_date)
    _check_feasible(inp, start, n_weeks, typed_cfg)
    weeks = _weeks(inp, start, n_weeks, typed_cfg)
    zones = zones_from_vdot(inp.vdot, typed_cfg.zones)
    roles = ROLES[inp.runs_per_week]
    end = start + timedelta(weeks=n_weeks)

    plan_fixed, warnings = _fixed_instances(commitments or [], start, end - timedelta(days=1))
    plan_fixed = [*(i for i in fixed or [] if start <= i.date < end), *plan_fixed]
    race_days = {
        start + timedelta(days=offset): DayConstraint(
            date=start + timedelta(days=offset),
            max_intensity="blocked",
            reason="Race day" if offset == (inp.goal_date - start).days else "After the race",
        )
        for offset in range((inp.goal_date - start).days, (end - start).days)
    }
    busy_events = busy or []
    tail = list(prior_tail or [])
    output: list[PlannedSession] = []
    dropped: list[DroppedSession] = []
    relaxed_weeks: set[date] = set()
    reduced_weeks: set[date] = set()

    growth = 1.0 + typed_cfg.progression.max_weekly_increase
    unfloored_cfg = typed_cfg.model_copy(
        update={
            "scheduler": typed_cfg.scheduler.model_copy(update={"max_run_volume_reduction": 1.0})
        }
    )
    target_peak = max(w.target_km for w in weeks if w.label != "taper")
    # Progress from the km actually composed, not the ideal targets, so that weeks the load
    # cap held back don't leave later weeks needing more than the cap allows.
    achieved_km = inp.current_weekly_km
    achieved_peak_km = achieved_km
    first_fixed = [i for i in plan_fixed if start <= i.date < start + timedelta(days=7)]
    first_week = replace(weeks[0], target_km=min(weeks[0].target_km, achieved_km * growth))
    first_load = estimate_combined_leg_load(
        start,
        _week_sessions(first_week, 0, roles, zones, typed_cfg),
        first_fixed,
        inp.climbing_target_per_week,
        typed_cfg,
    )
    # The load cap compares each week with the last non-cut-back, pre-taper week.
    reference = first_load / growth
    for index, week in enumerate(weeks):
        if week.label == "taper":
            ceiling = achieved_peak_km * week.target_km / target_peak
        elif week.cutback:
            ceiling = achieved_km * typed_cfg.progression.cutback_factor
        else:
            ceiling = achieved_km * growth
        week = replace(week, target_km=min(week.target_km, ceiling))
        sessions = _week_sessions(week, index, roles, zones, typed_cfg)
        week_end = week.start + timedelta(days=7)
        fixed_this_week = [i for i in plan_fixed if week.start <= i.date < week_end]

        # Runs alone must fit the week's allowance; that trim is the generator's own overshoot
        # (e.g. tempo/interval load swings), so it ignores the floor protecting runs from
        # football and climbing, which the second compose applies.
        taper_growth = 1.0 if week.label == "taper" else None  # a taper never grows
        runs_only = compose_week(
            week.start,
            sessions,
            [],
            reference,
            0,
            week.cutback,
            unfloored_cfg,
            weekly_growth_factor=taper_growth,
        ).sessions
        composition = compose_week(
            week.start,
            runs_only,
            fixed_this_week,
            reference,
            inp.climbing_target_per_week,
            week.cutback,
            typed_cfg,
            weekly_growth_factor=taper_growth,
        )
        schedule = schedule_week(
            week.start,
            composition.sessions,
            0,
            fixed_this_week,
            busy_events,
            tail,
            typed_cfg,
            timezone_name=timezone_name,
            day_constraints=race_days,
        )
        placed = [p.session for p in schedule.placed if p.session is not None]
        output.extend(placed)
        dropped.extend(schedule.dropped)
        warnings.extend(composition.warnings)
        warnings.extend(schedule.warnings)
        if schedule.relaxed:
            relaxed_weeks.add(week.start)
        if schedule.dropped:
            reduced_weeks.add(week.start)
        tail = placed
        if not week.cutback:  # taper weeks are non-cut-back weeks, so they become the reference
            reference = composition.actual_leg_load
        if not week.cutback and week.label != "taper":
            planned_run_km = sum(s.distance_m or 0 for s in sessions)
            composed_run_km = sum(
                s.distance_m or 0 for s in composition.sessions if s.sport == "run"
            )
            trimmed = composed_run_km / planned_run_km if planned_run_km else 1.0
            achieved_km = week.target_km * min(1.0, trimmed)
            achieved_peak_km = max(achieved_peak_km, achieved_km)

    taper_weeks = [w.start for w in weeks if w.label == "taper"]
    violations = check_plan(
        output,
        typed_cfg,
        fixed=plan_fixed,
        busy=busy_events,
        timezone_name=timezone_name,
        relaxed_weeks=relaxed_weeks,
        day_constraints=race_days,
        plan_start=start,
        taper_start=taper_weeks[0] if taper_weeks else None,
        exempt_load_weeks=reduced_weeks,
    )
    return PlanGenerationResult(
        sessions=output, dropped=dropped, warnings=warnings, violations=violations
    )


def generate_race_plan(
    inp: PlanInput,
    today: date,
    cfg: ConfigNode,
    *,
    commitments: list[Commitment] | None = None,
    busy: list[Busy] | None = None,
    fixed: list[FixedInstance] | None = None,
    prior_tail: list[PlannedSession] | None = None,
    timezone_name: str = "Europe/London",
) -> list[PlannedSession]:
    """Return the scheduled sessions of a race plan (see generate_race_plan_result)."""
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
