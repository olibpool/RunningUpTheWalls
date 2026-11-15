"""Training stats for the dashboard: pure functions over stored activities and sessions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from runcoach.display import monday
from runcoach.domain.models import Activity, Session
from runcoach.engine.continuous import WeekLoad, acr
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


def load_ratio(weeks: list[WeekStat], chronic_weeks: int) -> float | None:
    """Acute:chronic ratio of the last completed week; the current week is still filling."""
    return acr([WeekLoad(w.start, w.load) for w in weeks[:-1]], chronic_weeks)


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
