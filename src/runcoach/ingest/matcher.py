from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from runcoach.config import MatchingConfig
from runcoach.domain.models import Activity, Session

_DAY_OFFSET_COST = 0.5  # the spec's score for a session one day away from the activity


@dataclass(frozen=True)
class Match:
    activity_id: int
    session_id: int
    score: float


def local_day(started_at: str, timezone_name: str = "Europe/London") -> date:
    """The local calendar date an activity started on (started_at is UTC ISO 8601)."""
    return datetime.fromisoformat(started_at).astimezone(ZoneInfo(timezone_name)).date()


def match(
    activities: list[Activity],
    sessions: list[Session],
    cfg: MatchingConfig,
    timezone_name: str = "Europe/London",
) -> list[Match]:
    """Pair unmatched activities with planned sessions; ties stay unmatched for the human."""
    taken: set[int] = set()
    matches: list[Match] = []
    for activity in sorted(activities, key=lambda a: a.started_at):
        if activity.id is None or activity.session_id is not None:
            continue
        day = local_day(activity.started_at, timezone_name)
        scored: list[tuple[float, int]] = []
        for session in sessions:
            offset = abs((session.date - day).days)
            if (
                session.id in taken
                or session.status != "planned"
                or session.sport != activity.sport
                or offset > 1
            ):
                continue
            planned = session.duration_min or 0
            duration_gap = abs(activity.duration_s / 60 - planned) / planned if planned else 1.0
            score = _DAY_OFFSET_COST * offset + duration_gap
            if score < cfg.max_score:
                scored.append((score, session.id))
        scored.sort()
        if not scored or (len(scored) > 1 and math.isclose(scored[0][0], scored[1][0])):
            continue
        score, session_id = scored[0]
        matches.append(Match(activity.id, session_id, score))
        taken.add(session_id)
    return matches
