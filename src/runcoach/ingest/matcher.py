from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from runcoach.domain.models import Activity, PlannedSession


@dataclass(frozen=True)
class Match:
    activity: Activity
    session: PlannedSession
    score: tuple[int, float]


def _activity_date(activity: Activity) -> date:
    started = activity.started_at
    if "T" not in started:
        return date.fromisoformat(started)
    return date.fromisoformat(started.split("T", maxsplit=1)[0])


def match(
    activities: Iterable[Activity],
    sessions: Iterable[PlannedSession],
    cfg: object,
    *,
    max_day_delta: int = 2,
) -> list[Match]:
    """Return the best candidate matching for each activity, skipping ties and impossible fits."""
    sessions_list = list(sessions)
    matches: list[Match] = []
    used_session_ids: set[int] = set()

    for activity in activities:
        candidates = [
            session
            for session in sessions_list
            if session.sport == activity.sport and id(session) not in used_session_ids
        ]
        if not candidates:
            continue

        activity_day = _activity_date(activity)
        same_day = [session for session in candidates if session.date == activity_day]
        if same_day:
            candidates = same_day

        best_match: PlannedSession | None = None
        best_score: tuple[int, float] | None = None
        for session in candidates:
            day_delta = abs((session.date - activity_day).days)
            if day_delta > max_day_delta:
                continue

            distance_delta = 0.0
            if session.distance_m is not None and activity.distance_m is not None:
                distance_delta = abs(session.distance_m - activity.distance_m)
            score = (day_delta, distance_delta)
            if best_score is None or score < best_score:
                best_score = score
                best_match = session

        if best_match is None or best_score is None:
            continue

        if any(
            other.session == best_match and other.score == best_score
            for other in matches
        ):
            continue

        matches.append(Match(activity=activity, session=best_match, score=best_score))
        used_session_ids.add(id(best_match))

    return matches


def match_activity_to_session(
    activity: Activity,
    sessions: Iterable[PlannedSession],
    *,
    max_day_delta: int = 2,
) -> PlannedSession | None:
    """Match an imported activity to the most likely planned session."""
    matches = match([activity], sessions, None, max_day_delta=max_day_delta)
    return matches[0].session if matches else None
