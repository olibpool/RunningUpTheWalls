from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from runcoach.config import ConfigNode, SchedulingConfig, load_scheduling_config
from runcoach.domain.models import PlannedSession
from runcoach.engine.events import DayConstraint
from runcoach.engine.scheduler import Busy, FixedInstance


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


def _typed_config(cfg: ConfigNode | SchedulingConfig | None) -> SchedulingConfig | None:
    if isinstance(cfg, SchedulingConfig):
        return cfg
    if isinstance(cfg, ConfigNode):
        return load_scheduling_config(cfg)
    if cfg is None:
        return None
    raise TypeError("cfg must be a ConfigNode, SchedulingConfig, or None")


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
    cfg: ConfigNode | SchedulingConfig | None,
    *,
    fixed: list[FixedInstance] | None = None,
    busy: list[Busy] | None = None,
    timezone_name: str = "Europe/London",
    relaxed_weeks: set[date] | None = None,
    day_constraints: dict[date, DayConstraint] | None = None,
) -> list[Violation]:
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
                if typed_cfg is not None and (slot.fixed or other.fixed)
                else timedelta(
                    hours=typed_cfg.scheduler.min_hours_same_day_gap if typed_cfg is not None else 6
                )
            )
            if other.start - slot.end < required_gap:
                violations.append(
                    Violation(
                        "same-day-gap",
                        f"{slot.title} and {other.title} do not have the required same-day gap.",
                    )
                )

    if typed_cfg is None:
        minimum_hard_hours = 48
        minimum_climb_hours = 48
        buffer_minutes = 0
    else:
        minimum_hard_hours = typed_cfg.scheduler.min_hours_between_hard_leg
        minimum_climb_hours = typed_cfg.scheduler.min_hours_between_limit_climbs
        buffer_minutes = typed_cfg.availability.buffer_minutes

    hard_slots = [slot for slot in slots if slot.hard_leg]
    for left, right in zip(hard_slots, hard_slots[1:]):
        week = _week_start(right.start.date())
        minimum = (
            typed_cfg.scheduler.relaxed_hours_between_hard_leg
            if typed_cfg is not None and week in (relaxed_weeks or set())
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

    if typed_cfg is not None:
        max_long_run = typed_cfg.progression.max_long_run_minutes
        for session in sessions:
            if session.kind == "long" and session.duration_min is not None:
                if session.duration_min > max_long_run:
                    violations.append(Violation("long-run", "Long run exceeds the configured cap"))
    else:
        for session in sessions:
            if session.kind == "long" and session.distance_m and session.distance_m > 20000:
                violations.append(Violation("long-run", "Long run exceeds the configured cap"))

    return violations
