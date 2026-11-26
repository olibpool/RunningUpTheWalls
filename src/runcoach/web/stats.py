"""Training stats for the dashboard: pure functions over stored activities and sessions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil
from typing import Protocol

from runcoach.display import monday
from runcoach.domain.models import Activity, Session
from runcoach.ingest.matcher import local_day

COUNTED = {"planned", "done", "skipped"}  # moved and dropped sessions no longer count


@dataclass(frozen=True)
class Bar:
    label: str
    value: float
    title: str
    current: bool = False
    href: str | None = None


class HasWeekKm(Protocol):
    @property
    def start(self) -> date: ...
    @property
    def run_km(self) -> float: ...


def km_bars(weeks: Sequence[HasWeekKm], this_monday: date, link: bool = False) -> list[Bar]:
    """One column per week of run km, the current week marked; optionally linking to it."""
    return [
        Bar(
            label=f"{w.start:%d %b}",
            value=w.run_km,
            title=f"Week of {w.start:%d %b}: {w.run_km:.1f} km",
            current=w.start == this_monday,
            href=f"/week?start={w.start}" if link else None,
        )
        for w in weeks
    ]


@dataclass(frozen=True)
class WeekStat:
    start: date
    run_km: float
    load: float


@dataclass(frozen=True)
class Recent:
    run_km: float
    runs: int
    hours: float


@dataclass(frozen=True)
class WeekProgress:
    done: int
    total: int
    km_done: float
    km_planned: float

    @property
    def share(self) -> float:
        return self.done / self.total if self.total else 0.0


def weekly(activities: list[Activity], this_monday: date, weeks: int) -> list[WeekStat]:
    """Run km and load for each of the last `weeks` weeks, the current week last."""
    starts = [this_monday - timedelta(weeks=n) for n in range(weeks - 1, -1, -1)]
    km = dict.fromkeys(starts, 0.0)
    load = dict.fromkeys(starts, 0.0)
    for activity in activities:
        start = monday(local_day(activity.started_at))
        if start in km:
            if activity.sport == "run":
                km[start] += (activity.distance_m or 0) / 1000
            load[start] += activity.load or 0
    return [WeekStat(s, round(km[s], 1), round(load[s])) for s in starts]


def recent(activities: list[Activity], today: date, days: int) -> Recent:
    """Totals over the last `days` days, today included."""
    since = today - timedelta(days=days - 1)
    window = [a for a in activities if local_day(a.started_at) >= since]
    runs = [a for a in window if a.sport == "run"]
    return Recent(
        run_km=round(sum(a.distance_m or 0 for a in runs) / 1000, 1),
        runs=len(runs),
        hours=round(sum(a.duration_s for a in window) / 3600, 1),
    )


def completion(sessions: list[Session], today: date) -> tuple[int, int]:
    """Sessions done, out of those whose day has passed."""
    past = [s for s in sessions if s.date < today and s.status in COUNTED]
    return sum(s.status == "done" for s in past), len(past)


def week_progress(sessions: list[Session], activities: list[Activity]) -> WeekProgress:
    """One week's sessions done and run km against plan; `activities` are that week's."""
    counted = [s for s in sessions if s.status in COUNTED]
    return WeekProgress(
        done=sum(s.status == "done" for s in counted),
        total=len(counted),
        km_done=round(sum(a.distance_m or 0 for a in activities if a.sport == "run") / 1000, 1),
        km_planned=round(
            sum(s.distance_m or 0 for s in counted if s.sport == "run" and s.status != "skipped")
            / 1000,
            1,
        ),
    )


SPORT_ORDER = ("run", "football", "climb", "other")


@dataclass(frozen=True)
class SportShare:
    sport: str
    done: float
    ahead: float  # still planned this week


def balance(
    done: Sequence[tuple[str, float]], ahead: Sequence[tuple[str, float]]
) -> list[SportShare]:
    """A week's leg load by sport: what is done and what is still planned, in a fixed order."""
    totals = {sport: [0.0, 0.0] for sport in SPORT_ORDER}
    for index, items in enumerate((done, ahead)):
        for sport, load in items:
            totals[sport if sport in totals else "other"][index] += load
    return [SportShare(s, round(d), round(a)) for s, (d, a) in totals.items() if d or a]


@dataclass(frozen=True)
class Cell:
    day: date
    sport: str | None  # the day's biggest activity by load
    level: int  # 0 (nothing) to 4 (the heaviest days)
    title: str


def activity_grid(activities: Sequence[Activity], today: date, weeks: int) -> list[list[Cell]]:
    """Week rows (oldest first) of seven day cells, shaded by that day's training load."""
    start = monday(today) - timedelta(weeks=weeks - 1)
    by_day: dict[date, dict[str, float]] = {}
    for a in activities:
        day = local_day(a.started_at)
        if start <= day <= today:
            loads = by_day.setdefault(day, {})
            loads[a.sport] = loads.get(a.sport, 0.0) + (a.load or a.duration_s / 60)
    top = max((sum(v.values()) for v in by_day.values()), default=0.0)
    rows = []
    for w in range(weeks):
        row = []
        for d in range(7):
            day = start + timedelta(weeks=w, days=d)
            loads = by_day.get(day, {})
            total = sum(loads.values())
            sport = max(loads, key=loads.__getitem__) if loads else None
            level = min(4, ceil(total * 4 / top)) if total else 0
            what = ", ".join(sorted(loads)) if loads else "rest"
            row.append(Cell(day, sport, level if day <= today else 0, f"{day:%a %d %b}: {what}"))
        rows.append(row)
    return rows


def streak(activities: Sequence[Activity], today: date, per_week: int) -> int:
    """Weeks in a row with at least `per_week` activities; the current week counts once it
    gets there, and doesn't break the streak while it is still filling."""
    counts: dict[date, int] = {}
    for a in activities:
        week = monday(local_day(a.started_at))
        counts[week] = counts.get(week, 0) + 1
    week = monday(today)
    run = 1 if counts.get(week, 0) >= per_week else 0
    week -= timedelta(weeks=1)
    while counts.get(week, 0) >= per_week:
        run += 1
        week -= timedelta(weeks=1)
    return run


@dataclass(frozen=True)
class Records:
    longest_run: Activity | None
    fastest_run: Activity | None  # best average pace over at least `min_km`
    biggest_week: tuple[date, float] | None  # (Monday, run km)
    counts: dict[str, int]  # activities per sport


def records(activities: Sequence[Activity], min_km: float) -> Records:
    """Personal bests from everything stored."""
    runs = [a for a in activities if a.sport == "run" and a.distance_m]
    long_enough = [a for a in runs if (a.distance_m or 0) >= min_km * 1000 and a.duration_s]
    weeks: dict[date, float] = {}
    for a in runs:
        week = monday(local_day(a.started_at))
        weeks[week] = weeks.get(week, 0.0) + (a.distance_m or 0) / 1000
    counts: dict[str, int] = {}
    for a in activities:
        counts[a.sport] = counts.get(a.sport, 0) + 1
    return Records(
        longest_run=max(runs, key=lambda a: a.distance_m or 0, default=None),
        fastest_run=min(
            long_enough, key=lambda a: a.duration_s / (a.distance_m or 1), default=None
        ),
        biggest_week=max(weeks.items(), key=lambda kv: kv[1], default=None),
        counts=counts,
    )


@dataclass(frozen=True)
class StackBar:
    label: str
    parts: list[tuple[str, float]]  # (sport, value) in SPORT_ORDER
    title: str
    current: bool = False
    href: str | None = None
    note: str | None = None  # e.g. the phase that starts this week

    @property
    def value(self) -> float:
        return sum(v for _, v in self.parts)


def stack_bars(
    weeks: Sequence[tuple[date, dict[str, float]]],
    this_monday: date,
    *,
    link: bool = False,
    notes: dict[date, str] | None = None,
) -> list[StackBar]:
    """One stacked column per week of load by sport, the current week marked."""
    bars = []
    for start, loads in weeks:
        parts = [(s, float(round(loads.get(s, 0.0)))) for s in SPORT_ORDER if loads.get(s)]
        detail = ", ".join(f"{s} {v:.0f}" for s, v in parts) or "nothing"
        bars.append(
            StackBar(
                label=f"{start:%d %b}",
                parts=parts,
                title=f"Week of {start:%d %b}: {detail}",
                current=start == this_monday,
                href=f"/week?start={start}" if link else None,
                note=(notes or {}).get(start),
            )
        )
    return bars
