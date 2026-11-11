from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from runcoach.domain.models import PlannedSession


@dataclass(frozen=True)
class Violation:
    kind: str
    message: str


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def check_plan(sessions: list[PlannedSession], cfg: object) -> list[Violation]:
    violations: list[Violation] = []
    if not sessions:
        return violations

    sessions_by_week: dict[date, list[PlannedSession]] = {}
    for session in sessions:
        sessions_by_week.setdefault(_week_start(session.date), []).append(session)

    for week_sessions in sessions_by_week.values():
        if not any(session.kind == "rest" for session in week_sessions):
            violations.append(Violation("no-rest-day", "No rest day in the week"))

        hard_session_days: list[date] = [
            session.date
            for session in week_sessions
            if session.kind in {"tempo", "intervals", "long"}
        ]
        for index in range(len(hard_session_days) - 1):
            if (hard_session_days[index + 1] - hard_session_days[index]).days <= 1:
                violations.append(
                    Violation(
                        "consecutive-hard",
                        "Hard sessions are too close together",
                    )
                )

    for index, session in enumerate(sessions):
        if session.kind == "long" and session.distance_m and session.distance_m > 20000:
            violations.append(Violation("long-run", "Long run exceeds the configured cap"))
        if session.kind in {"tempo", "intervals", "long"} and index + 1 < len(sessions):
            next_session = sessions[index + 1]
            if next_session.kind in {"tempo", "intervals", "long"}:
                violations.append(
                    Violation("consecutive-hard", "Hard sessions are too close together")
                )

    return violations
