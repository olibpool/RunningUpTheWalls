from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from math import ceil

from runcoach.config import ConfigNode, SchedulingConfig, load_scheduling_config
from runcoach.domain.models import Athlete, Checkin, Commitment, PlannedSession
from runcoach.engine.events import (
    CommitmentOccurrence,
    DayConstraint,
    day_constraints,
    expand_commitments,
    merge_constraint,
)
from runcoach.engine.habits import Habit
from runcoach.engine.ramp import RampPlan, ramp_for_block
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
    within_long_share,
    within_spike_limit,
)
from runcoach.engine.time_trial import time_trial_session
from runcoach.engine.travel import minimal_travel_plan
from runcoach.engine.zones import Zones, zones_from_vdot


@dataclass(frozen=True)
class WeekLoad:
    week_start: date
    leg_load: float
    blocked: bool = False


@dataclass(frozen=True)
class History:
    weekly_loads: list[WeekLoad]
    typical_easy_duration_min: int
    prior_tail: list[PlannedSession] = field(default_factory=list)
    checkins: list[Checkin] = field(default_factory=list)
    busy: list[Busy] = field(default_factory=list)
    plan_start_date: date | None = None
    longest_recent_run_km: float | None = None  # longest run in the spike window
    recent_long_run_km: float | None = None  # longest planned long run done in that window
    weekly_run_minutes: float = 0.0  # recent running time a week, the floor for planned running
    weekly_run_load: float = 0.0  # recent running leg load a week, at the efforts you logged
    week_so_far: dict[str, float] = field(default_factory=dict)  # done leg load this week, by sport
    habits: dict[str, Habit] = field(default_factory=dict)  # usual days and times by sport


@dataclass(frozen=True)
class ACRStatus:
    ratio: float | None
    warning: str | None
    hold_next_week: bool


@dataclass(frozen=True)
class ContinuousPlanResult:
    sessions: list[PlannedSession]
    targets: list[float]
    allowed_loads: list[float]
    dropped: list[DroppedSession]
    warnings: list[str]
    violations: list[Violation]


def chronic_load(weekly_loads: list[WeekLoad], n: int) -> float:
    if n <= 0:
        raise ValueError("n must be positive")
    if any(item.leg_load < 0 for item in weekly_loads):
        raise ValueError("weekly leg load must not be negative")
    available = sorted(
        (item for item in weekly_loads if not item.blocked),
        key=lambda item: item.week_start,
    )[-n:]
    if not available:
        return 0.0
    return sum(item.leg_load for item in available) / len(available)


def acr(weekly_loads: list[WeekLoad], chronic_weeks: int) -> float | None:
    if not weekly_loads:
        return None
    ordered = sorted(weekly_loads, key=lambda item: item.week_start)
    acute = ordered[-1]
    if acute.leg_load < 0:
        raise ValueError("weekly leg load must not be negative")
    chronic = chronic_load(ordered[:-1], chronic_weeks)
    return acute.leg_load / chronic if chronic > 0 else None


def evaluate_acr(weekly_loads: list[WeekLoad], cfg: SchedulingConfig) -> ACRStatus:
    ratio = acr(weekly_loads, cfg.continuous.chronic_load_weeks)
    if ratio is None:
        return ACRStatus(ratio=None, warning=None, hold_next_week=False)
    warning = None
    if ratio < cfg.continuous.acr_low or ratio > cfg.continuous.acr_high:
        warning = (
            f"Last week's load was {ratio:.2f}x your recent average (aim for "
            f"{cfg.continuous.acr_low:.2f}-{cfg.continuous.acr_high:.2f}x), "
            + ("so go easy on extra sessions." if ratio > 1 else "so build back gradually.")
        )
    return ACRStatus(
        ratio=ratio,
        warning=warning,
        hold_next_week=ratio > cfg.continuous.acr_hold,
    )


def _fixed_instances(
    occurrences: list[CommitmentOccurrence],
) -> tuple[list[FixedInstance], list[str]]:
    fixed: list[FixedInstance] = []
    warnings: list[str] = []
    for event in occurrences:
        if event.kind != "fixed":
            continue
        if event.start_time is None or event.duration_min is None:
            warnings.append(
                f"Skipped fixed commitment {event.title}: start time and duration are required."
            )
            continue
        fixed.append(
            FixedInstance(
                date=event.date,
                title=event.title,
                sport=event.sport or "other",
                start_time=event.start_time,
                duration_min=event.duration_min,
                rpe=event.rpe,
                kind=event.kind,
            )
        )
    return fixed, warnings


def block_intervals(events: list[Commitment]) -> list[tuple[date, date]]:
    intervals: list[tuple[date, date]] = []
    for event in events:
        if event.kind != "block" or event.start_date is None or event.end_date is None:
            continue
        start = date.fromisoformat(event.start_date)
        end = date.fromisoformat(event.end_date)
        if end < start:
            raise ValueError(f"Block {event.title!r} ends before it starts")
        intervals.append((start, end))
    merged: list[tuple[date, date]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + timedelta(days=1):
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def ramp_for_week(
    week_start: date,
    intervals: list[tuple[date, date]],
    cfg: SchedulingConfig,
) -> tuple[float, bool, bool]:
    """Return (load factor, quality forbidden, returning from a block)."""
    completed = [(start, end) for start, end in intervals if end < week_start]
    if not completed:
        return 1.0, False, False
    block_start, block_end = max(completed, key=lambda interval: interval[1])
    plan: RampPlan = ramp_for_block((block_end - block_start).days + 1, cfg)
    ramp_week = max(0, (week_start - (block_end + timedelta(days=1))).days // 7)
    factor = plan.weekly_multipliers[ramp_week] if ramp_week < len(plan.weekly_multipliers) else 1.0
    # The ramp climbs back to prior chronic load, so its weeks (and the one after) may rise >cap.
    returning = ramp_week <= len(plan.weekly_multipliers)
    return factor, ramp_week < plan.no_quality_weeks, returning


def _quality_kind(profile: str, at_ceiling: bool) -> str | None:
    selected = "threshold" if at_ceiling and profile == "aerobic" else profile
    if selected == "threshold":
        return "tempo"
    if selected == "speed":
        return "intervals"
    return None


def _easy_runs(
    week_start: date,
    run_budget: float,
    count: int,
    zones: Zones,
    typed_cfg: SchedulingConfig,
) -> list[PlannedSession]:
    """Split a load budget into equal easy runs, fewer rather than too short ones."""
    per_minute_load = typed_cfg.rpe.easy * typed_cfg.load_factors.run_leg
    while count and run_budget / count / per_minute_load < typed_cfg.templates.min_easy_minutes:
        count -= 1
    if not count:
        return []
    minutes = round(run_budget / count / per_minute_load)
    return [
        build_session("easy", WeekContext(week_start, minutes), zones, typed_cfg).model_copy(
            update={"reason": "Aerobic maintenance."}
        )
        for _ in range(count)
    ]


def default_ceiling(
    athlete: Athlete,
    zones: Zones,
    fixed: list[FixedInstance],
    week_start: date,
    baseline: float,
    cfg: SchedulingConfig,
) -> float:
    """Weekly load ceiling when the athlete set none: the race plans' km-per-run cap as mostly
    easy running, plus a usual week's football and climbing; never below current load."""
    easy_min_per_km = (zones.easy.min_s_per_km + zones.easy.max_s_per_km) / 2 / 60
    run_km = athlete.runs_per_week * cfg.progression.max_weekly_km_per_run
    run_load = run_km * easy_min_per_km * cfg.continuous.ceiling_run_rpe * cfg.load_factors.run_leg
    week_fixed = [i for i in fixed if week_start <= i.date < week_start + timedelta(days=7)]
    other = estimate_combined_leg_load(
        week_start, [], week_fixed, athlete.climbing_target_per_week, cfg
    )
    return max(run_load + other, baseline)


def _sessions_for_week(
    week_start: date,
    run_budget: float,
    runs_per_week: int,
    profile: str,
    at_ceiling: bool,
    zones: Zones,
    typed_cfg: SchedulingConfig,
    progress: float = 0.0,
    quality_allowed: bool = True,
    longest_km: float | None = None,
    min_long_minutes: int = 0,
) -> list[PlannedSession]:
    """A week's runs from its load budget: the quality session at its real cost first, then a
    long run with easy runs at most `easy_max_share_of_long` of it; fewer runs, never tiny ones,
    and a long run no shorter than `min_long_minutes` (your usual run) while easy runs can go."""
    if runs_per_week < 2 or runs_per_week > 6:
        raise ValueError("runs_per_week must be between 2 and 6")
    if run_budget <= 0:
        return []
    t = typed_cfg.templates
    rpe = typed_cfg.rpe
    per_load = typed_cfg.load_factors.run_leg
    quality: list[PlannedSession] = []
    kind = _quality_kind(profile, at_ceiling) if quality_allowed else None
    easy_min_per_km = (zones.easy.min_s_per_km + zones.easy.max_s_per_km) / 2 / 60
    week_minutes = run_budget / (rpe.easy * per_load)  # as if all easy: an upper bound
    if kind is not None:
        shortest = quality_minutes(kind, 0.0, typed_cfg)
        planned = min(
            quality_minutes(kind, progress, typed_cfg),
            max(
                shortest,
                quality_cap_minutes(kind, week_minutes / easy_min_per_km, zones, typed_cfg),
            ),
        )
        # The hard part stays within its share of running time (80/20); at low volume the
        # shortest version is tried, and below that the week is all easy running.
        for minutes in dict.fromkeys((planned, shortest)):
            hard = minutes - t.warmup_min - t.cooldown_min
            if hard <= week_minutes * t.max_quality_share:
                context = WeekContext(week_start, minutes, progress)
                session = build_session(kind, context, zones, typed_cfg)
                quality.append(
                    session.model_copy(update={"reason": f"{profile.title()} emphasis."})
                )
                break
    remaining = run_budget - sum(s.leg_load for s in quality)
    ratio = t.easy_max_share_of_long
    others = runs_per_week - 1 - len(quality)
    long_limit = float(typed_cfg.progression.max_long_run_minutes)
    if longest_km is not None:  # no run more than the allowed step over the recent longest
        spike_km = longest_km * (1 + typed_cfg.progression.max_single_run_increase)
        long_limit = min(long_limit, spike_km * easy_min_per_km)
    share = typed_cfg.progression.max_long_run_share
    quality_minutes_total = sum(s.duration_min or 0 for s in quality)
    # A coach sets the long run first (your usual long run, or its share of the week), then
    # shares out the rest as easy runs; when those hit their cap the long run takes the rest.
    low, high = t.long_share
    target = max(
        min_long_minutes, (low + (high - low) * progress) * remaining / (rpe.easy * per_load)
    )
    while True:
        balanced = remaining / ((rpe.long + others * ratio * rpe.easy) * per_load)
        long_min = min(max(target, balanced), long_limit, remaining / (rpe.long * per_load))
        easy_load = remaining - long_min * rpe.long * per_load
        easy_min = (
            min(ratio * long_min, easy_load / (others * rpe.easy * per_load)) if others else 0
        )
        capped = long_min
        if len(quality) + others + 1 >= typed_cfg.progression.long_share_min_runs:
            others_minutes = quality_minutes_total + others * easy_min
            capped = min(long_min, share / (1 - share) * others_minutes)
        if not others or (
            easy_min >= t.min_easy_minutes and capped >= min(min_long_minutes, long_limit)
        ):
            long_min = capped
            break
        others -= 1  # fewer, proper runs instead of several tiny ones and a "long" run that isn't
    if long_min < t.min_easy_minutes:
        return []
    # Strides are part of the run's time, not extra on top of the budget.
    stride_min = t.strides_repeats * (t.stride_s + t.stride_recovery_s) / 60
    easy = [
        build_session(
            "easy",
            WeekContext(week_start, round(easy_min - (stride_min if i == 0 else 0)), progress,
                        strides=i == 0),
            zones, typed_cfg,
        ).model_copy(update={"reason": "Aerobic maintenance."})
        for i in range(others)
    ]  # fmt: skip
    rest = [*quality, *easy]
    long_minutes = round(long_min)
    if len(rest) + 1 >= typed_cfg.progression.long_share_min_runs:  # few-run weeks are exempt
        share_cap = typed_cfg.progression.max_long_run_share
        others_minutes = sum(session.duration_min or 0 for session in rest)
        long_minutes = max(1, min(long_minutes, int(share_cap / (1 - share_cap) * others_minutes)))
    long_run = build_session("long", WeekContext(week_start, long_minutes), zones, typed_cfg)
    return [long_run.model_copy(update={"reason": "Long aerobic session."}), *rest]


def generate_continuous_plan_result(
    athlete: Athlete,
    history: History,
    today: date,
    events: list[Commitment],
    cfg: ConfigNode,
    *,
    not_before: date | datetime | None = None,
) -> ContinuousPlanResult:
    """`not_before` is the first day sessions may use (default `today`), or the current moment
    so slots already past today are skipped."""
    typed_cfg = load_scheduling_config(cfg)
    zones = zones_from_vdot(athlete.vdot, typed_cfg.zones)
    baseline = chronic_load(
        history.weekly_loads,
        n=typed_cfg.continuous.chronic_load_weeks,
    )
    if baseline <= 0:
        raise ValueError("Continuous planning requires positive non-blocked chronic load")
    acr_status = evaluate_acr(history.weekly_loads, typed_cfg)
    if history.typical_easy_duration_min <= 0:
        raise ValueError("typical_easy_duration_min must be positive")
    if not typed_cfg.continuous.block_cycle:
        raise ValueError("continuous.block_cycle must not be empty")

    first_monday = today - timedelta(days=today.weekday())
    plan_start = history.plan_start_date or first_monday
    plan_start_monday = plan_start - timedelta(days=plan_start.weekday())
    if plan_start_monday > first_monday:
        raise ValueError("history.plan_start_date must not be after the current plan week")
    weeks = typed_cfg.continuous.weeks_ahead
    plan_end = first_monday + timedelta(weeks=weeks)
    occurrences = expand_commitments(events, today, plan_end - timedelta(days=1))
    fixed_instances, warnings = _fixed_instances(occurrences)
    constraints = day_constraints(occurrences, history.checkins, typed_cfg)
    all_constraints = dict(constraints)
    blocks = block_intervals(events)
    prior_tail = list(history.prior_tail)
    sessions_out: list[PlannedSession] = []
    dropped: list[DroppedSession] = []
    targets: list[float] = []
    allowed_loads: list[float] = []
    if acr_status.warning is not None:
        warnings.append(acr_status.warning)
    relaxed_weeks: set[date] = set()
    exempt_weeks: set[date] = set()
    previous_target = baseline
    max_increase = min(
        typed_cfg.continuous.weekly_increase,
        typed_cfg.progression.max_weekly_increase,
    )
    try:
        vdot_updated = datetime.fromisoformat(athlete.vdot_updated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("athlete.vdot_updated_at must be an ISO 8601 UTC timestamp") from exc
    if vdot_updated.tzinfo is None or vdot_updated.utcoffset() != timedelta(0):
        raise ValueError("athlete.vdot_updated_at must be an ISO 8601 UTC timestamp")
    next_retest_date = vdot_updated.astimezone(timezone.utc).date() + timedelta(
        weeks=typed_cfg.continuous.retest_every_weeks
    )

    ceiling = athlete.max_weekly_load or default_ceiling(
        athlete, zones, fixed_instances, first_monday, baseline, typed_cfg
    )
    # What you say you can run now lifts what sparse records show (a coach would ask).
    easy_min_per_km = (zones.easy.min_s_per_km + zones.easy.max_s_per_km) / 2 / 60
    stated_run_load = (
        (athlete.weekly_km or 0) * easy_min_per_km * typed_cfg.rpe.easy
        * typed_cfg.load_factors.run_leg
    )  # fmt: skip
    # The long run's floor: your usual run, your stated long run, or the last long run you did;
    # it grows with the plan, so each re-plan carries on from the long runs you've done.
    long_floor = max(
        float(history.typical_easy_duration_min),
        max(athlete.long_run_km or 0, history.recent_long_run_km or 0) * easy_min_per_km,
    )
    longest_known = max(history.longest_recent_run_km or 0, athlete.long_run_km or 0)
    recent_runs: list[tuple[date, float]] = (
        [(first_monday - timedelta(days=1), longest_known)] if longest_known else []
    )
    for week_index in range(weeks):
        week_start = first_monday + timedelta(weeks=week_index)
        week_end = week_start + timedelta(days=7)
        program_week = (week_start - plan_start_monday).days // 7
        week_fixed = [
            item for item in fixed_instances if max(today, week_start) <= item.date < week_end
        ]
        cutback = (program_week + 1) % typed_cfg.progression.cutback_every_n_weeks == 0
        growth = typed_cfg.progression.cutback_factor if cutback else 1.0 + max_increase
        if week_index and not cutback:
            long_floor *= 1 + max_increase
        # Growth is measured from the last full week, so a cut-back doesn't lower the base.
        target = (
            previous_target
            if week_index == 0 and acr_status.hold_next_week
            else previous_target * growth
        )
        target = min(target, ceiling)
        effective_growth = target / previous_target
        hard_fixed = sum(1 for item in week_fixed if item.sport == "football")
        quality_allowed = 1 + hard_fixed < typed_cfg.scheduler.max_hard_leg_days_per_week
        targets.append(target)
        at_ceiling = target >= ceiling

        profile = typed_cfg.continuous.block_cycle[
            (program_week // typed_cfg.continuous.weeks_per_block)
            % len(typed_cfg.continuous.block_cycle)
        ]
        ramp_factor, no_quality, returning = ramp_for_week(week_start, blocks, typed_cfg)
        if returning:
            exempt_weeks.add(week_start)
        progress = (program_week % typed_cfg.continuous.weeks_per_block) / max(
            1, typed_cfg.continuous.weeks_per_block - 1
        )
        week_constraints = {
            day: constraint
            for day, constraint in constraints.items()
            if week_start <= day < week_end
        }
        travel_days = {
            occurrence.date
            for occurrence in occurrences
            if occurrence.kind == "block"
            and occurrence.minimal_plan
            and week_start <= occurrence.date < week_end
        }
        minimal_week = bool(travel_days)
        if minimal_week:
            for offset in range(7):
                day = week_start + timedelta(days=offset)
                existing = week_constraints.get(day)
                if day in travel_days and existing is not None and existing.travel_allowed:
                    continue
                block_constraint = DayConstraint(
                    date=day,
                    max_intensity="blocked",
                    reason="Travel block allows only its minimal running plan.",
                    sources=frozenset({"minimal_plan"}),
                )
                week_constraints[day] = merge_constraint(existing, block_constraint)
        if no_quality and not minimal_week:
            for offset in range(7):
                day = week_start + timedelta(days=offset)
                ramp_constraint = DayConstraint(
                    date=day,
                    max_intensity="easy",
                    reason="Return ramp: quality sessions are temporarily disabled.",
                    sources=frozenset({"ramp"}),
                )
                week_constraints[day] = merge_constraint(week_constraints.get(day), ramp_constraint)
        all_constraints.update(week_constraints)

        # A mid-week re-plan counts what you've already done this week, like a credit.
        done = history.week_so_far if week_index == 0 else {}
        credit_load = sum(c.credit_leg_load for c in week_constraints.values()) + sum(done.values())
        climb_target = 0 if minimal_week else athlete.climbing_target_per_week
        non_run_load = estimate_combined_leg_load(
            week_start,
            [],
            week_fixed,
            climb_target,
            typed_cfg,
        )
        effective_load = target * ramp_factor
        run_budget = max(0.0, effective_load - non_run_load - credit_load)
        # Football and climbing as entered can fill the whole target; running never drops below
        # your recent running (its time as easy minutes, or the load it put on your legs if that
        # is more), scaled like the target by ramps and cut-backs.
        # ... and never below a couple of short easy runs, so someone back from a break, or
        # whose football fills the week, still has running to build on.
        # The smallest week with that many runs: easy runs of the minimum length, and a long run
        # they are the allowed share of.
        runs = min(athlete.runs_per_week, typed_cfg.continuous.min_runs_per_week)
        shortest = typed_cfg.templates.min_easy_minutes
        long_min = ceil(shortest / typed_cfg.templates.easy_max_share_of_long)
        fewest_runs = (runs - 1) * shortest * typed_cfg.rpe.easy + long_min * typed_cfg.rpe.long
        run_floor = (
            0.0
            if minimal_week
            else max(
                max(history.weekly_run_minutes * typed_cfg.rpe.easy, fewest_runs if runs else 0)
                * typed_cfg.load_factors.run_leg,
                history.weekly_run_load,
                stated_run_load,
            )
            * ramp_factor
            * (typed_cfg.progression.cutback_factor if cutback else 1.0)
        )
        run_floor = max(0.0, run_floor - done.get("run", 0.0))
        if run_budget < run_floor:
            run_budget = run_floor
            effective_load = non_run_load + credit_load + run_floor
            target = effective_load / ramp_factor
            targets[-1] = target
            effective_growth = target / previous_target
        allowed_loads.append(effective_load)
        time_trial_due = (
            week_start >= next_retest_date and cutback and not minimal_week and not no_quality
        )
        longest = longest_in_window(recent_runs, week_start, typed_cfg)
        trial = time_trial_session(week_start, typed_cfg) if time_trial_due else None
        include_time_trial = trial is not None and trial.leg_load <= run_budget
        if minimal_week:
            week_sessions = minimal_travel_plan(
                week_start, history.typical_easy_duration_min, typed_cfg
            )
        elif trial is not None and include_time_trial:
            easy = _easy_runs(
                week_start, run_budget - trial.leg_load, athlete.runs_per_week - 1, zones,
                typed_cfg,
            )  # fmt: skip
            week_sessions = [trial, *easy]
        else:
            if trial is not None:
                warnings.append(
                    "Time trial deferred because its estimated load exceeds the "
                    "cut-back week's remaining load allowance."
                )
            week_sessions = _sessions_for_week(
                week_start, run_budget, athlete.runs_per_week, profile, at_ceiling, zones,
                typed_cfg, progress, quality_allowed, longest,
                round(long_floor * (typed_cfg.progression.cutback_factor if cutback else 1)),
            )  # fmt: skip
        week_sessions = within_long_share(
            [within_spike_limit(s, longest, typed_cfg) for s in week_sessions], typed_cfg
        )
        composition = compose_week(
            week_start,
            week_sessions,
            week_fixed,
            previous_target,
            climb_target,
            cutback,
            typed_cfg,
            ramp_factor=ramp_factor,
            weekly_growth_factor=effective_growth,
            credited_leg_load=credit_load,
        )
        schedule = schedule_week(
            week_start,
            composition.sessions,
            0,
            [i for i in fixed_instances if i.date >= today],
            history.busy,
            prior_tail,
            typed_cfg,
            timezone_name=athlete.timezone,
            not_before=(not_before or today) if week_index == 0 else None,
            day_constraints=week_constraints,
            habits=history.habits,
        )
        sessions_out.extend(
            placement.session for placement in schedule.placed if placement.session is not None
        )
        dropped.extend(schedule.dropped)
        warnings.extend(composition.warnings)
        warnings.extend(schedule.warnings)
        if schedule.relaxed:
            relaxed_weeks.add(week_start)
        if schedule.dropped or minimal_week:
            exempt_weeks.add(week_start)
        prior_tail = [
            placement.session for placement in schedule.placed if placement.session is not None
        ]
        if include_time_trial and any(session.kind == "time_trial" for session in prior_tail):
            next_retest_date = week_start + timedelta(weeks=typed_cfg.continuous.retest_every_weeks)
        recent_runs += [
            (s.date, (s.distance_m or 0) / 1000) for s in prior_tail if s.sport == "run"
        ]
        if not cutback:
            # Grow from what was actually planned, which trimming may have left below target;
            # holiday, travel and return weeks have their own rules, so they keep the target.
            planned = composition.actual_leg_load
            exempt = week_start in exempt_weeks or planned <= 0
            previous_target = target if exempt else min(target, planned)

    violations = check_plan(
        sessions_out,
        typed_cfg,
        fixed=fixed_instances,
        busy=history.busy,
        timezone_name=athlete.timezone,
        relaxed_weeks=relaxed_weeks,
        day_constraints=all_constraints,
        plan_start=(not_before.date() if isinstance(not_before, datetime) else not_before) or today,
        exempt_load_weeks=exempt_weeks,
    )
    return ContinuousPlanResult(
        sessions=sessions_out,
        targets=targets,
        allowed_loads=allowed_loads,
        dropped=dropped,
        warnings=warnings,
        violations=violations,
    )
