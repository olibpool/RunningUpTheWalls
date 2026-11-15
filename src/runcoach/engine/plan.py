from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

from runcoach.config import ConfigNode, SchedulingConfig, load_scheduling_config
from runcoach.domain.models import DISTANCE_METRES, Commitment, Distance, PlanInput, PlannedSession
from runcoach.engine.continuous import block_intervals, ramp_for_week
from runcoach.engine.events import DayConstraint, day_constraints, expand_commitments
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
from runcoach.engine.templates import (
    WeekContext,
    build_session,
    longest_in_window,
    quality_cap_minutes,
    quality_minutes,
    race_session,
    within_spike_limit,
)
from runcoach.engine.zones import Zones, zones_from_vdot

ROLES: dict[int, list[str]] = {
    2: ["quality", "long"],
    3: ["easy", "quality", "long"],
    4: ["easy", "quality", "easy", "long"],
    5: ["easy", "quality", "easy", "quality", "long"],
    6: ["easy", "quality", "easy", "quality", "recovery", "long"],
}
# Base builds threshold before VO2max work; peak leans towards what the race demands.
_BASE_ROTATION = ("tempo",)
_BUILD_ROTATION = ("tempo", "intervals")
_PEAK_ROTATION: dict[Distance, tuple[str, ...]] = {
    "5k": ("intervals", "intervals", "tempo"),
    "10k": ("intervals", "tempo"),
    "half": ("tempo", "tempo", "intervals"),
    "marathon": ("tempo",),  # plus marathon-pace long runs
}
_RACE_LABELS: dict[Distance, str] = {
    "5k": "5K", "10k": "10K", "half": "half marathon", "marathon": "marathon"
}  # fmt: skip


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


def _capped_by_runs(inp: PlanInput, cfg: SchedulingConfig) -> SchedulingConfig:
    """Peak weekly km is also limited by the runs a week, so easy runs stay easy."""
    per_runs = inp.runs_per_week * cfg.progression.max_weekly_km_per_run
    peaks = {**cfg.peak_weekly_km, inp.distance: min(cfg.peak_weekly_km[inp.distance], per_runs)}
    updates: dict[str, object] = {"peak_weekly_km": peaks}
    if inp.distance == "marathon":  # the long run carries a bigger share of marathon training
        updates["progression"] = cfg.progression.model_copy(
            update={"max_long_run_share": cfg.progression.marathon_max_long_run_share}
        )
        updates["templates"] = cfg.templates.model_copy(
            update={"long_share": cfg.templates.marathon_long_share}
        )
    return cfg.model_copy(update=updates)


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
    if cfg.peak_weekly_km[inp.distance] < needed:
        raise InfeasibleGoal(
            f"A {inp.distance} plan needs at least {needed:g} km a week at its peak, but "
            f"{inp.runs_per_week} runs a week top out at {cfg.peak_weekly_km[inp.distance]:g} km; "
            "add a run a week."
        )
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


def _quality_allowance(roles: list[str], fixed: list[FixedInstance], cfg: SchedulingConfig) -> int:
    """Quality runs that fit beside the long run and hard fixed sessions such as football."""
    hard_fixed = sum(1 for i in fixed if i.sport == "football" or i.kind == "race")
    others = int("long" in roles) + hard_fixed
    return max(0, cfg.scheduler.max_hard_leg_days_per_week - others)


def _limit_quality(roles: list[str], allowed: int) -> list[str]:
    """Turn quality slots beyond `allowed` into easy runs."""
    kept: list[str] = []
    for role in roles:
        if role == "quality":
            role = "quality" if allowed > 0 else "easy"
            allowed -= role == "quality"
        kept.append(role)
    return kept


def _rotation(week: _Week, distance: Distance) -> tuple[str, ...]:
    if week.label == "base":
        return _BASE_ROTATION
    if week.label == "build":
        return _BUILD_ROTATION
    return _PEAK_ROTATION[distance]


def _week_sessions(
    week: _Week,
    index: int,
    roles: list[str],
    zones: Zones,
    cfg: SchedulingConfig,
    distance: Distance,
) -> list[PlannedSession]:
    """Turn a weekly km target into templated sessions for the week's roles."""
    t = cfg.templates
    easy_pace = (zones.easy.min_s_per_km + zones.easy.max_s_per_km) / 2  # s/km
    if week.race_week:
        # Race week: one short sharpener and short shakeout runs; no long run.
        roles = _limit_quality([role for role in roles if role != "long"], 1)
        roles = roles[: cfg.race.week_max_runs]
        week = replace(week, progress=0.0)

    quality: list[PlannedSession] = []
    quality_cap = week.target_km * easy_pace / 60 * t.max_quality_share
    rotation = _rotation(week, distance)
    for slot, role in enumerate(role for role in roles if role == "quality"):
        kind = rotation[(index + slot) % len(rotation)]
        minutes = min(
            quality_minutes(kind, week.progress, cfg),
            round(quality_cap),
            max(
                quality_minutes(kind, 0.0, cfg),
                quality_cap_minutes(kind, week.target_km, zones, cfg),
            ),
        )
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
    strides_due = True  # strides every week: leg speed and form at almost no load
    for role in easy_roles:
        minutes = max(t.min_easy_minutes, round(unit_km * weights[role] * easy_pace / 60))
        # The long run stays clearly the longest run of the week.
        minutes = max(1, min(minutes, round(long_target * t.easy_max_share_of_long)))
        if week.race_week:
            minutes = min(max(minutes, t.min_easy_minutes), cfg.race.week_easy_max_min)
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
        # Marathon peak: every other long run finishes at goal pace.
        finish = distance == "marathon" and week.label == "peak" and index % 2 == 0
        context = WeekContext(week.start, long_minutes, marathon_finish=finish)
        sessions.append(build_session("long", context, zones, cfg))

    note = f"{week.label.title()} block"
    if week.cutback:
        note += ", cut-back week"
    if week.race_week:
        note += ", race week"
    return [s.model_copy(update={"reason": note}) for s in sessions]


def fixed_instances(
    commitments: list[Commitment], start: date, end: date
) -> tuple[list[FixedInstance], list[str]]:
    """Fixed commitments in [start, end] as scheduler instances, plus skip warnings."""
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
    typed_cfg = _capped_by_runs(inp, load_scheduling_config(cfg))
    start = _start_monday(today)
    n_weeks = _week_count(start, inp.goal_date)
    _check_feasible(inp, start, n_weeks, typed_cfg)
    weeks = _weeks(inp, start, n_weeks, typed_cfg)
    zones = zones_from_vdot(inp.vdot, typed_cfg.zones)
    roles = ROLES[inp.runs_per_week]
    end = start + timedelta(weeks=n_weeks)

    plan_fixed, warnings = fixed_instances(commitments or [], start, end - timedelta(days=1))
    plan_fixed = [*(i for i in fixed or [] if start <= i.date < end), *plan_fixed]
    race_days = {
        start + timedelta(days=offset): DayConstraint(
            date=start + timedelta(days=offset),
            max_intensity="blocked",
            reason="Race day" if offset == (inp.goal_date - start).days else "After the race",
        )
        for offset in range((inp.goal_date - start).days, (end - start).days)
    }
    race = race_session(
        inp.goal_date,
        DISTANCE_METRES[inp.distance],
        _RACE_LABELS[inp.distance],
        inp.vdot,
        typed_cfg,
    )
    # The scheduler sees the race as a fixed hard day: it keeps a rest day and spaces quality.
    race_fixed = FixedInstance(
        date=race.date,
        title=race.title,
        sport="run",
        start_time=typed_cfg.race.start_time,
        duration_min=race.duration_min or 0,
        rpe=race.rpe,
        kind="race",
    )
    taper_in = {
        inp.goal_date - timedelta(days=n): DayConstraint(
            date=inp.goal_date - timedelta(days=n),
            max_intensity="easy",
            reason="Easy before the race",
        )
        for n in range(1, typed_cfg.race.easy_days_before + 1)
    }
    # Drinks nights, holidays and active trips constrain days like they do in continuous mode.
    occurrences = expand_commitments(commitments or [], start, end - timedelta(days=1))
    event_limits = day_constraints([o for o in occurrences if o.kind != "fixed"], [], typed_cfg)
    constraints = {
        **taper_in,
        **event_limits,  # a drinks night or holiday is stricter than an easy day
        **race_days,
    }
    blocks = block_intervals(commitments or [])
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
        _week_sessions(first_week, 0, roles, zones, typed_cfg, inp.distance),
        first_fixed,
        inp.climbing_target_per_week,
        typed_cfg,
    )
    # The load cap compares each week with the last non-cut-back, pre-taper week.
    reference = first_load / growth
    # Runs from the last 30 days (the athlete's longest, then the plan's own) bound each new run.
    recent_runs: list[tuple[date, float]] = (
        [(start - timedelta(days=1), inp.longest_recent_run_km)]
        if inp.longest_recent_run_km
        else []
    )
    for index, week in enumerate(weeks):
        if week.label == "taper":
            ceiling = achieved_peak_km * week.target_km / target_peak
        elif week.cutback:
            ceiling = achieved_km * typed_cfg.progression.cutback_factor
        else:
            ceiling = achieved_km * growth
        week = replace(week, target_km=min(week.target_km, ceiling))
        # Judged from the week's Sunday, so a holiday ending mid-week makes this week ramp week 0.
        ramp, no_quality, returning = ramp_for_week(
            week.start + timedelta(days=6), blocks, typed_cfg
        )
        week_end = week.start + timedelta(days=7)
        fixed_this_week = [i for i in plan_fixed if week.start <= i.date < week_end]
        week_roles = _limit_quality(roles, 0 if no_quality else _quality_allowance(
            roles, fixed_this_week, typed_cfg
        ))  # fmt: skip
        sessions = _week_sessions(week, index, week_roles, zones, typed_cfg, inp.distance)
        longest = longest_in_window(recent_runs, week.start, typed_cfg)
        sessions = [within_spike_limit(s, longest, typed_cfg) for s in sessions]
        credited = sum(
            c.credit_leg_load for d, c in constraints.items() if week.start <= d < week_end
        )
        if returning:
            reduced_weeks.add(week.start)  # the return ramp climbs back faster than the weekly cap

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
            ramp_factor=ramp,
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
            ramp_factor=ramp,
            weekly_growth_factor=taper_growth,
            credited_leg_load=credited,
        )
        schedule = schedule_week(
            week.start,
            composition.sessions,
            0,
            [*fixed_this_week, race_fixed] if week.race_week else fixed_this_week,
            busy_events,
            tail,
            typed_cfg,
            timezone_name=timezone_name,
            day_constraints=constraints,
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
        recent_runs += [(s.date, (s.distance_m or 0) / 1000) for s in placed if s.sport == "run"]
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

    output.append(race)
    reduced_weeks.add(weeks[-1].start)  # race week's load is mostly the race
    taper_weeks = [w.start for w in weeks if w.label == "taper"]
    # Race day is blocked for everything but the race itself.
    check_constraints = {d: c for d, c in constraints.items() if d != inp.goal_date}
    violations = check_plan(
        output,
        typed_cfg,
        fixed=plan_fixed,
        busy=busy_events,
        timezone_name=timezone_name,
        relaxed_weeks=relaxed_weeks,
        day_constraints=check_constraints,
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


def plan_snapshot(inp: PlanInput, commitments: list[Commitment]) -> dict[str, object]:
    """What a saved plan was built from, including which events it already accounts for."""
    return {
        **inp.model_dump(mode="json"),
        "event_ids": [c.id for c in commitments if c.kind != "fixed" and c.id is not None],
    }
