from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from runcoach.domain.models import Activity, Adjustment, Checkin, PlannedSession


@dataclass(frozen=True)
class Change:
    session_id: int | None
    action: str
    updates: dict[str, object] = field(default_factory=dict)
    rule: str = "unknown"
    reason: str = ""


@dataclass
class PlanState:
    sessions: list[PlannedSession]
    activities: list[Activity]
    checkins: list[Checkin]
    athlete: object


def _matched_session_ids(state: PlanState) -> set[int]:
    matched: set[int] = set()
    for session in state.sessions:
        for activity in state.activities:
            if activity.sport != session.sport:
                continue
            if activity.started_at.startswith(session.date.isoformat()):
                matched.add(id(session))
    return matched


def evaluate(state: PlanState, today: date, cfg: object) -> list[Change]:
    """Return rule-based session changes for the current plan state."""
    changes: list[Change] = []
    matched_ids = _matched_session_ids(state)

    for session in state.sessions:
        if session.date < today and id(session) not in matched_ids:
            changes.append(
                Change(
                    session_id=None,
                    action="modify",
                    updates={"status": "skipped"},
                    rule="missed_session",
                    reason=(
                        f"Session missed on {session.date.isoformat()}. Do not cram the workout "
                        "into the next day; keep the plan stable."
                    ),
                )
            )

    week_counts: dict[date, int] = {}
    for session in state.sessions:
        if session.date < today:
            week_start = session.date - timedelta(days=session.date.weekday())
            week_counts[week_start] = week_counts.get(week_start, 0) + 1
    for week_start, count in week_counts.items():
        if count >= 2:
            changes.append(
                Change(
                    session_id=None,
                    action="modify",
                    updates={
                        "reason": (
                            "Repeat prior week volume after missed sessions in "
                            f"{week_start.isoformat()}"
                        )
                    },
                    rule="missed_two_plus",
                    reason=(
                        f"Two or more missed sessions in week {week_start.isoformat()}; "
                        "repeat the prior week's volume instead of increasing load."
                    ),
                )
            )

    for checkin in state.checkins:
        if checkin.ill or checkin.niggle:
            changes.append(
                Change(
                    session_id=None,
                    action="modify",
                    updates={"reason": "illness or niggle: scale quality down to easy"},
                    rule="ill_or_niggle",
                    reason=(
                        f"Check-in for {checkin.date.isoformat()} flags a niggle or illness; "
                        "downgrade quality sessions to easy for recovery."
                    ),
                )
            )

    return changes


def missed_session_adjustment(
    planned: PlannedSession,
    *,
    created_at: str = "1970-01-01T00:00:00+00:00",
    session_id: int | None = None,
) -> Adjustment:
    """Return a deterministic missed-session adjustment record."""
    return Adjustment(
        id=None,
        created_at=created_at,
        session_id=session_id,
        rule="missed_session",
        before_json=None,
        after_json=None,
        reason=(
            "Session missed. Do not cram the workout into the next day; keep the plan stable "
            f"and re-evaluate the week around {planned.date.isoformat()}."
        ),
    )


def repeat_week_volume_adjustment(
    planned: PlannedSession,
    *,
    created_at: str = "1970-01-01T00:00:00+00:00",
    session_id: int | None = None,
) -> Adjustment:
    """Return the rule-based adjustment for repeated-volume weeks after missed sessions."""
    return Adjustment(
        id=None,
        created_at=created_at,
        session_id=session_id,
        rule="repeat_week_volume",
        before_json=None,
        after_json=None,
        reason=(
            f"Two or more sessions were missed in week {planned.date.isoformat()}; "
            "repeat the previous week's volume instead of increasing load."
        ),
    )


def _session_date(session: PlannedSession) -> date:
    return session.date
