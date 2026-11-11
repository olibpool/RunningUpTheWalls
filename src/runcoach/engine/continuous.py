from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from runcoach.config import ConfigNode, SchedulingConfig, load_scheduling_config
from runcoach.domain.models import Athlete, Checkin, Commitment, PlannedSession
from runcoach.engine.events import (
    CommitmentOccurrence,
    DayConstraint,
    day_constraints,
    expand_commitments,
)
from runcoach.engine.ramp import RampPlan, ramp_for_block
from runcoach.engine.safety import check_plan
from runcoach.engine.scheduler import (
    Busy,
    DroppedSession,
    FixedInstance,
    compose_week,
    estimate_combined_leg_load,
    schedule_week,
)
from runcoach.engine.time_trial import time_trial_session
from runcoach.engine.travel import minimal_travel_plan


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
            f"ACR {ratio:.2f} is outside the configured "
            f"{cfg.continuous.acr_low:.2f}-{cfg.continuous.acr_high:.2f} range."
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


def _block_intervals(events: list[Commitment]) -> list[tuple[date, date]]:
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


def _ramp_for_week(
    week_start: date,
    intervals: list[tuple[date, date]],
    cfg: SchedulingConfig,
) -> tuple[float, bool]:
    completed = [(start, end) for start, end in intervals if end < week_start]
    if not completed:
        return 1.0, False
    block_start, block_end = max(completed, key=lambda interval: interval[1])
    plan: RampPlan = ramp_for_block((block_end - block_start).days + 1, cfg)
    ramp_week = max(0, (week_start - (block_end + timedelta(days=1))).days // 7)
    factor = plan.weekly_multipliers[ramp_week] if ramp_week < len(plan.weekly_multipliers) else 1.0
    return factor, ramp_week < plan.no_quality_weeks


def _merge_constraint(left: DayConstraint | None, right: DayConstraint) -> DayConstraint:
    if left is None:
        return right
    order = {"normal": 0, "easy": 1, "blocked": 2}
    rpe_values = [value for value in (left.max_rpe, right.max_rpe) if value is not None]
    easy_sports = (
        None
        if left.easy_sports is None or right.easy_sports is None
        else left.easy_sports | right.easy_sports
    )
    block_sources = "block" in left.sources and "block" in right.sources
    travel_allowed = (
        left.travel_allowed and right.travel_allowed
        if block_sources
        else right.travel_allowed
        if "block" in right.sources
        else left.travel_allowed
    )
    return DayConstraint(
        date=left.date,
        max_intensity=max(left.max_intensity, right.max_intensity, key=order.__getitem__),
        reason="; ".join(dict.fromkeys((left.reason, right.reason))),
        max_rpe=min(rpe_values) if rpe_values else None,
        credit_leg_load=left.credit_leg_load + right.credit_leg_load,
        sources=left.sources | right.sources,
        easy_sports=easy_sports,
        travel_allowed=travel_allowed,
    )


def _quality_kind(profile: str, at_ceiling: bool) -> str | None:
    selected = "threshold" if at_ceiling and profile == "aerobic" else profile
    if selected == "threshold":
        return "tempo"
    if selected == "speed":
        return "intervals"
    return None


def _sessions_for_week(
    week_start: date,
    run_budget: float,
    runs_per_week: int,
    profile: str,
    at_ceiling: bool,
    cfg: ConfigNode,
    typed_cfg: SchedulingConfig,
) -> list[PlannedSession]:
    if runs_per_week < 2 or runs_per_week > 6:
        raise ValueError("runs_per_week must be between 2 and 6")
    if run_budget <= 0:
        return []
    run_factor = typed_cfg.load_factors.run_leg
    long_rpe = float(cfg.rpe.long)
    easy_rpe = float(cfg.rpe.easy)
    quality_kind = _quality_kind(profile, at_ceiling)
    quality_rpe = float(cfg.rpe.tempo) if quality_kind == "tempo" else float(cfg.rpe.intervals)
    long_share = typed_cfg.progression.max_long_run_share
    quality_share = typed_cfg.continuous.quality_load_share if quality_kind else 0.0
    if quality_kind is not None and runs_per_week == 2:
        quality_share = 1.0 - long_share

    long_load = run_budget * long_share
    quality_load = run_budget * quality_share
    easy_count = runs_per_week - 1 - int(quality_kind is not None)
    easy_load = max(0.0, run_budget - long_load - quality_load) / easy_count if easy_count else 0.0
    specifications = [
        ("long", "Long run", long_rpe, long_load, "Long aerobic session."),
    ]
    if quality_kind is not None:
        title = "Tempo" if quality_kind == "tempo" else "Intervals"
        specifications.append(
            (quality_kind, title, quality_rpe, quality_load, f"{profile.title()} emphasis.")
        )
    specifications.extend(
        ("easy", "Easy run", easy_rpe, easy_load, "Aerobic maintenance.") for _ in range(easy_count)
    )
    minimum_load = sum(rpe * run_factor for _, _, rpe, _, _ in specifications)
    if run_budget < minimum_load:
        return []

    sessions: list[PlannedSession] = []
    for kind, title, rpe, target_load, reason in specifications:
        duration = max(1, round(target_load / (rpe * run_factor)))
        if kind == "long":
            duration = min(duration, typed_cfg.progression.max_long_run_minutes)
        load = duration * rpe * run_factor
        sessions.append(
            PlannedSession(
                date=week_start,
                sport="run",
                kind=kind,
                title=title,
                duration_min=duration,
                rpe=rpe,
                leg_load=load,
                reason=reason,
            )
        )
    return sessions


def generate_continuous_plan_result(
    athlete: Athlete,
    history: History,
    today: date,
    events: list[Commitment],
    cfg: ConfigNode,
) -> ContinuousPlanResult:
    typed_cfg = load_scheduling_config(cfg)
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
    blocks = _block_intervals(events)
    prior_tail = list(history.prior_tail)
    sessions_out: list[PlannedSession] = []
    dropped: list[DroppedSession] = []
    targets: list[float] = []
    allowed_loads: list[float] = []
    if acr_status.warning is not None:
        warnings.append(acr_status.warning)
    relaxed_weeks: set[date] = set()
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

    for week_index in range(weeks):
        week_start = first_monday + timedelta(weeks=week_index)
        week_end = week_start + timedelta(days=7)
        program_week = (week_start - plan_start_monday).days // 7
        week_fixed = [
            item for item in fixed_instances if max(today, week_start) <= item.date < week_end
        ]
        cutback = (program_week + 1) % typed_cfg.progression.cutback_every_n_weeks == 0
        growth = typed_cfg.progression.cutback_factor if cutback else 1.0 + max_increase
        target = (
            previous_target
            if week_index == 0 and acr_status.hold_next_week
            else previous_target * growth
        )
        if athlete.max_weekly_load is not None:
            target = min(target, athlete.max_weekly_load)
        effective_growth = target / previous_target
        targets.append(target)
        at_ceiling = athlete.max_weekly_load is not None and target >= athlete.max_weekly_load

        profile = typed_cfg.continuous.block_cycle[
            (program_week // typed_cfg.continuous.weeks_per_block)
            % len(typed_cfg.continuous.block_cycle)
        ]
        ramp_factor, no_quality = _ramp_for_week(week_start, blocks, typed_cfg)
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
                week_constraints[day] = _merge_constraint(existing, block_constraint)
        if no_quality and not minimal_week:
            for offset in range(7):
                day = week_start + timedelta(days=offset)
                ramp_constraint = DayConstraint(
                    date=day,
                    max_intensity="easy",
                    reason="Return ramp: quality sessions are temporarily disabled.",
                    sources=frozenset({"ramp"}),
                )
                week_constraints[day] = _merge_constraint(
                    week_constraints.get(day), ramp_constraint
                )
        all_constraints.update(week_constraints)

        credit_load = sum(constraint.credit_leg_load for constraint in week_constraints.values())
        climb_target = 0 if minimal_week else athlete.climbing_target_per_week
        non_run_load = estimate_combined_leg_load(
            week_start,
            [],
            week_fixed,
            climb_target,
            typed_cfg,
        )
        effective_load = target * ramp_factor
        allowed_loads.append(effective_load)
        run_budget = max(0.0, effective_load - non_run_load - credit_load)
        time_trial_due = (
            week_start >= next_retest_date and cutback and not minimal_week and not no_quality
        )
        include_time_trial = False
        if minimal_week:
            week_sessions = minimal_travel_plan(
                week_start,
                history.typical_easy_duration_min,
                typed_cfg,
            )
        elif time_trial_due:
            trial = time_trial_session(week_start, typed_cfg)
            if trial.leg_load <= run_budget:
                week_sessions = [trial]
                include_time_trial = True
            else:
                warnings.append(
                    "Time trial deferred because its estimated load exceeds the "
                    "cut-back week's remaining load allowance."
                )
                week_sessions = _sessions_for_week(
                    week_start,
                    run_budget,
                    athlete.runs_per_week,
                    profile,
                    at_ceiling,
                    cfg,
                    typed_cfg,
                )
        else:
            week_sessions = _sessions_for_week(
                week_start,
                run_budget,
                athlete.runs_per_week,
                profile,
                at_ceiling,
                cfg,
                typed_cfg,
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
            week_fixed,
            history.busy,
            prior_tail,
            typed_cfg,
            timezone_name=athlete.timezone,
            not_before=today if week_index == 0 else None,
            day_constraints=week_constraints,
        )
        sessions_out.extend(
            placement.session for placement in schedule.placed if placement.session is not None
        )
        dropped.extend(schedule.dropped)
        warnings.extend(composition.warnings)
        warnings.extend(schedule.warnings)
        if schedule.relaxed:
            relaxed_weeks.add(week_start)
        prior_tail = [
            placement.session for placement in schedule.placed if placement.session is not None
        ]
        if include_time_trial and any(session.kind == "time_trial" for session in prior_tail):
            next_retest_date = week_start + timedelta(weeks=typed_cfg.continuous.retest_every_weeks)
        previous_target = target

    warnings.extend(
        f"Safety check ({violation.kind}): {violation.message}"
        for violation in check_plan(
            sessions_out,
            typed_cfg,
            fixed=fixed_instances,
            busy=history.busy,
            timezone_name=athlete.timezone,
            relaxed_weeks=relaxed_weeks,
            day_constraints=all_constraints,
        )
    )
    return ContinuousPlanResult(
        sessions=sessions_out,
        targets=targets,
        allowed_loads=allowed_loads,
        dropped=dropped,
        warnings=warnings,
    )


def generate_continuous_plan(
    athlete: Athlete,
    history: History,
    today: date,
    events: list[Commitment],
    cfg: ConfigNode,
) -> list[PlannedSession]:
    return generate_continuous_plan_result(athlete, history, today, events, cfg).sessions
