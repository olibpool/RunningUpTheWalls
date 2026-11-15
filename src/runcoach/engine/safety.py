from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from runcoach.config import ConfigNode, SchedulingConfig, load_scheduling_config
from runcoach.domain.models import PlannedSession, Step
from runcoach.engine.events import DayConstraint
from runcoach.engine.scheduler import Busy, FixedInstance, fixed_leg_load


@dataclass(frozen=True)
class Violation:
    kind: str
    message: str


@dataclass(frozen=True)
class _Slot:
    start: datetime
    end: datetime
    title: str
    sport: str
    kind: str
    fixed: bool
    rpe: float | None
    reason: str

    @property
    def hard_leg(self) -> bool:
        return self.sport == "football" or self.kind in {
            "football",
            "long",
            "tempo",
            "intervals",
            "time_trial",
        }

    @property
    def limit_climb(self) -> bool:
        return self.sport == "climb" and self.kind == "climb_power"


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _typed_config(cfg: ConfigNode | SchedulingConfig) -> SchedulingConfig:
    if isinstance(cfg, SchedulingConfig):
        return cfg
    if isinstance(cfg, ConfigNode):
        return load_scheduling_config(cfg)
    raise TypeError("cfg must be a ConfigNode or SchedulingConfig")


def _work_paces(steps: list[Step], kind: str) -> list[float]:
    paces: list[float] = []
    for step in steps:
        if step.kind == "repeat":
            paces.extend(_work_paces(step.children, kind))
        elif step.kind == kind:
            paces.extend(p for p in (step.pace_min_s_per_km, step.pace_max_s_per_km) if p)
    return paces


def _pace_order_violations(sessions: list[PlannedSession]) -> list[Violation]:
    easy = [
        pace
        for s in sessions
        if s.kind in {"easy", "recovery", "long"}
        for pace in _work_paces(s.steps, "steady")
    ]
    tempo = [pace for s in sessions if s.kind == "tempo" for pace in _work_paces(s.steps, "work")]
    intervals = [
        pace for s in sessions if s.kind == "intervals" for pace in _work_paces(s.steps, "work")
    ]
    ordered = [group for group in (easy, tempo, intervals) if group]
    if any(min(slower) <= max(faster) for slower, faster in zip(ordered, ordered[1:])):
        return [Violation("pace-order", "Easy, threshold and interval paces are out of order.")]
    return []


def _load_violations(
    sessions: list[PlannedSession],
    fixed: list[FixedInstance],
    cfg: SchedulingConfig,
    plan_start: date | None,
    taper_start: date | None,
    exempt_weeks: set[date],
) -> list[Violation]:
    """Weekly leg-load rise, long-run share and taper checks over full plan weeks."""
    loads: dict[date, float] = {}
    run_minutes: dict[date, list[PlannedSession]] = {}
    for session in sessions:
        week = _week_start(session.date)
        loads[week] = loads.get(week, 0.0) + session.leg_load
        if session.sport == "run":
            run_minutes.setdefault(week, []).append(session)
    for instance in fixed:
        week = _week_start(instance.date)
        loads[week] = loads.get(week, 0.0) + fixed_leg_load(instance, cfg)
    if not loads:
        return []
    first = min(loads)
    if plan_start is not None:
        first = max(first, plan_start + timedelta(days=(7 - plan_start.weekday()) % 7))
    weeks: list[date] = []
    week = first
    while week <= max(loads):
        weeks.append(week)
        week += timedelta(days=7)

    violations: list[Violation] = []
    cap = 1.0 + cfg.progression.max_weekly_increase
    reference: float | None = None
    previous: float | None = None
    for week in weeks:
        load = loads.get(week, 0.0)
        # Compare against the last non-dip week, so cut-backs and tapers don't reset the base.
        if reference is not None and week not in exempt_weeks and load > reference * cap + 1e-6:
            violations.append(
                Violation(
                    "weekly-rise",
                    f"Week of {week.isoformat()} leg load {load:.0f} rises more than "
                    f"{cfg.progression.max_weekly_increase:.0%} over {reference:.0f}.",
                )
            )
        if previous is None or load >= previous:
            reference = load
        previous = load

        runs = run_minutes.get(week, [])
        total = sum(s.duration_min or 0 for s in runs)
        longest = max((s.duration_min or 0 for s in runs if s.kind == "long"), default=0)
        share = cfg.progression.max_long_run_share
        # One minute of slack: durations are whole minutes and are rounded after scaling.
        # Weeks with two or fewer runs are exempt: the long run can't be a small share there.
        few_runs = len(runs) <= 2
        if week not in exempt_weeks and not few_runs and total and longest > share * total + 1:
            violations.append(
                Violation(
                    "long-run-share",
                    f"Long run in week of {week.isoformat()} is {longest / total:.0%} "
                    f"of run time, above {share:.0%}.",
                )
            )

    if taper_start is not None:
        peak = max((loads.get(w, 0.0) for w in weeks if w < taper_start), default=0.0)
        for week in weeks:
            if week >= taper_start and loads.get(week, 0.0) > peak + 1e-6:
                violations.append(
                    Violation("taper-above-peak", f"Taper week of {week.isoformat()} exceeds peak.")
                )
    return violations


def _slots(
    sessions: list[PlannedSession],
    fixed: list[FixedInstance],
    tz: ZoneInfo,
) -> list[_Slot]:
    result = [
        _Slot(
            start=datetime.combine(
                session.date,
                time.fromisoformat(session.start_time) if session.start_time else time.min,
                tzinfo=tz,
            ),
            end=datetime.combine(
                session.date,
                time.fromisoformat(session.start_time) if session.start_time else time.min,
                tzinfo=tz,
            )
            + timedelta(minutes=session.duration_min or 0),
            title=session.title,
            sport=session.sport,
            kind=session.kind,
            fixed=False,
            rpe=session.rpe,
            reason=session.reason,
        )
        for session in sessions
    ]
    result.extend(
        _Slot(
            start=datetime.combine(
                instance.date,
                time.fromisoformat(instance.start_time),
                tzinfo=tz,
            ),
            end=datetime.combine(
                instance.date,
                time.fromisoformat(instance.start_time),
                tzinfo=tz,
            )
            + timedelta(minutes=instance.duration_min),
            title=instance.title,
            sport=instance.sport,
            kind=instance.kind if instance.sport == "climb" else instance.sport,
            fixed=True,
            rpe=instance.rpe,
            reason="Fixed commitment",
        )
        for instance in fixed
    )
    return sorted(result, key=lambda slot: slot.start)


def check_plan(
    sessions: list[PlannedSession],
    cfg: ConfigNode | SchedulingConfig,
    *,
    fixed: list[FixedInstance] | None = None,
    busy: list[Busy] | None = None,
    timezone_name: str = "Europe/London",
    relaxed_weeks: set[date] | None = None,
    day_constraints: dict[date, DayConstraint] | None = None,
    plan_start: date | None = None,
    taper_start: date | None = None,
    exempt_load_weeks: set[date] | None = None,
) -> list[Violation]:
    """Return every safety violation; an empty list means the plan may be saved.

    plan_start: a mid-week start makes its partial week exempt from the weekly-load checks.
    exempt_load_weeks: Mondays of weeks exempt from the rise and long-run-share checks
    (return ramps, or weeks where the scheduler had to drop sessions).
    """
    typed_cfg = _typed_config(cfg)
    fixed_instances = fixed or []
    busy_events = busy or []
    constraints = day_constraints or {}
    tz = ZoneInfo(timezone_name)
    slots = _slots(sessions, fixed_instances, tz)
    violations: list[Violation] = []
    occupied_by_week: dict[date, set[date]] = {}

    for session in sessions:
        if session.kind != "rest":
            occupied_by_week.setdefault(_week_start(session.date), set()).add(session.date)
    for instance in fixed_instances:
        occupied_by_week.setdefault(_week_start(instance.date), set()).add(instance.date)
    for days in occupied_by_week.values():
        if len(days) >= 7:
            violations.append(Violation("no-rest-day", "No rest day in the week"))

    for index, slot in enumerate(slots):
        for other in slots[index + 1 :]:
            if other.start.date() != slot.start.date():
                break
            required_gap = (
                timedelta(minutes=typed_cfg.availability.buffer_minutes)
                if slot.fixed or other.fixed
                else timedelta(hours=typed_cfg.scheduler.min_hours_same_day_gap)
            )
            if other.start - slot.end < required_gap:
                violations.append(
                    Violation(
                        "same-day-gap",
                        f"{slot.title} and {other.title} do not have the required same-day gap.",
                    )
                )

    minimum_hard_hours = typed_cfg.scheduler.min_hours_between_hard_leg
    minimum_climb_hours = typed_cfg.scheduler.min_hours_between_limit_climbs
    buffer_minutes = typed_cfg.availability.buffer_minutes

    hard_slots = [slot for slot in slots if slot.hard_leg]
    for left, right in zip(hard_slots, hard_slots[1:]):
        week = _week_start(right.start.date())
        minimum = (
            typed_cfg.scheduler.relaxed_hours_between_hard_leg
            if week in (relaxed_weeks or set())
            else minimum_hard_hours
        )
        if right.start - left.start < timedelta(hours=minimum):
            violations.append(
                Violation(
                    "hard-leg-spacing",
                    f"{left.title} and {right.title} are too close together.",
                )
            )

    climb_slots = [slot for slot in slots if slot.limit_climb]
    for left, right in zip(climb_slots, climb_slots[1:]):
        if right.start - left.start < timedelta(hours=minimum_climb_hours):
            violations.append(
                Violation(
                    "limit-climb-spacing",
                    f"{left.title} and {right.title} are too close together.",
                )
            )

    for slot in slots:
        constraint = constraints.get(slot.start.date())
        if constraint is not None:
            travel_run = (
                constraint.travel_allowed
                and slot.sport == "run"
                and slot.kind == "easy"
                and "Minimal run during travel" in slot.reason
            )
            is_easy_limited = constraint.max_intensity == "easy" and (
                constraint.easy_sports is None or slot.sport in constraint.easy_sports
            )
            violates_constraint = (
                (constraint.max_intensity == "blocked" and not travel_run)
                or (
                    constraint.max_rpe is not None
                    and slot.rpe is not None
                    and slot.rpe > constraint.max_rpe
                )
                or (
                    is_easy_limited
                    and slot.kind in {"tempo", "intervals", "long", "climb_power", "time_trial"}
                )
            )
            if violates_constraint:
                kind = "fixed-day-constraint" if slot.fixed else "day-constraint"
                violations.append(
                    Violation(
                        kind,
                        f"{slot.title} violates {constraint.max_intensity}: {constraint.reason}",
                    )
                )
        for event in busy_events:
            if event.start.tzinfo is None or event.end.tzinfo is None:
                violations.append(
                    Violation("invalid-busy-slot", f"{event.title} must be timezone-aware.")
                )
                continue
            if slot.start < event.end + timedelta(minutes=buffer_minutes) and slot.end > (
                event.start - timedelta(minutes=buffer_minutes)
            ):
                violations.append(
                    Violation("busy-overlap", f"{slot.title} conflicts with {event.title}.")
                )

    max_long_run = typed_cfg.progression.max_long_run_minutes
    for session in sessions:
        if session.kind == "long" and (session.duration_min or 0) > max_long_run:
            violations.append(Violation("long-run", "Long run exceeds the configured cap"))

    violations.extend(_pace_order_violations(sessions))
    violations.extend(
        _load_violations(
            sessions,
            fixed_instances,
            typed_cfg,
            plan_start,
            taper_start,
            exempt_load_weeks or set(),
        )
    )
    return violations
