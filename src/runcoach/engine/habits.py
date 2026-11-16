"""When you usually do each sport, learned from what you actually did, so plans land on your
climbing nights and run times instead of the first free slot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median

from runcoach.config import HabitsConfig


@dataclass(frozen=True)
class Habit:
    weekdays: frozenset[int]  # 0 = Monday
    start_time: str  # "HH:MM", local


def learn_habits(
    starts: list[tuple[str, datetime]], today: date, cfg: HabitsConfig
) -> dict[str, Habit]:
    """Habits by sport from (sport, local start) pairs in the last `window_weeks`: the weekdays
    done in at least `weekday_share` of those weeks, and the median start time."""
    since = today - timedelta(weeks=cfg.window_weeks)
    by_sport: dict[str, list[datetime]] = {}
    for sport, start in starts:
        if since <= start.date() < today:
            by_sport.setdefault(sport, []).append(start)
    habits: dict[str, Habit] = {}
    for sport, times in by_sport.items():
        if len(times) < cfg.min_sessions:
            continue
        weeks_per_day: dict[int, set[date]] = {}
        for t in times:
            weeks_per_day.setdefault(t.weekday(), set()).add(t.date() - timedelta(days=t.weekday()))
        days = frozenset(
            day
            for day, weeks in weeks_per_day.items()
            if len(weeks) >= cfg.weekday_share * cfg.window_weeks
        )
        minutes = median(t.hour * 60 + t.minute for t in times)
        rounded = int(round(minutes / cfg.round_minutes) * cfg.round_minutes) % (24 * 60)
        habits[sport] = Habit(days, f"{rounded // 60:02d}:{rounded % 60:02d}")
    return habits
