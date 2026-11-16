"""Move planned sessions that a calendar change now clashes with (pure)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from runcoach.adapt.rules import Change
from runcoach.config import SchedulingConfig
from runcoach.display import monday
from runcoach.domain.models import PlannedSession, Session
from runcoach.engine.events import DayConstraint
from runcoach.engine.scheduler import Busy, FixedInstance, schedule_week

RULE = "calendar_change"


def _clashes(session: Session, busy: list[Busy], buffer: timedelta, tz: ZoneInfo) -> bool:
    if session.start_time is None:
        return False
    start = datetime.combine(session.date, time.fromisoformat(session.start_time), tzinfo=tz)
    end = start + timedelta(minutes=session.duration_min or 0)
    return any(start < b.end + buffer and end > b.start - buffer for b in busy)


def reschedule_for_calendar(
    sessions: list[Session],
    fixed: list[FixedInstance],
    busy: list[Busy],
    today: date,
    cfg: SchedulingConfig,
    *,
    horizon_days: int = 14,
    day_constraints: dict[date, DayConstraint] | None = None,
    timezone_name: str = "Europe/London",
) -> list[Change]:
    """Re-schedule only the weeks where a planned session now overlaps a busy event."""
    tz = ZoneInfo(timezone_name)
    buffer = timedelta(minutes=cfg.availability.buffer_minutes)
    horizon = today + timedelta(days=horizon_days)
    upcoming = [s for s in sessions if s.status == "planned" and today <= s.date < horizon]
    weeks = sorted({monday(s.date) for s in upcoming if _clashes(s, busy, buffer, tz)})
    changes: list[Change] = []
    for week in weeks:
        movable = [s for s in upcoming if monday(s.date) == week]
        # Sessions already behind us keep their spacing constraints on what we move.
        prior: list[PlannedSession] = [
            s
            for s in sessions
            if s.status in {"planned", "done"} and week - timedelta(days=7) <= s.date < today
        ]
        result = schedule_week(
            week,
            list[PlannedSession](movable),
            0,
            fixed,
            busy,
            prior,
            cfg,
            timezone_name=timezone_name,
            not_before=today,
            day_constraints=day_constraints,
        )
        by_id = {s.id: s for s in movable}
        for placement in result.placed:
            moved = placement.session
            if not isinstance(moved, Session):
                continue
            before = by_id[moved.id]
            if (moved.date, moved.start_time) != (before.date, before.start_time):
                changes.append(
                    Change(
                        moved.id,
                        "modify",
                        {
                            "date": moved.date,
                            "start_time": moved.start_time,
                            "reason": moved.reason,
                        },
                        RULE,
                        f"Calendar changed: moved from {before.date:%a %d %b} {before.start_time} "
                        f"to {moved.date:%a %d %b} {moved.start_time}.",
                    )
                )
        for dropped in result.dropped:
            if isinstance(dropped.session, Session):
                changes.append(
                    Change(
                        dropped.session.id,
                        "modify",
                        {"status": "dropped"},
                        RULE,
                        f"Calendar changed: no slot left this week. {dropped.reason}",
                    )
                )
    return changes
