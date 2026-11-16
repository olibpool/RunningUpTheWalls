from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from runcoach import db
from runcoach.config import ConfigNode, load_scheduling_config
from runcoach.display import monday
from runcoach.domain.models import Session
from runcoach.engine.events import CommitmentOccurrence, day_constraints, expand_commitments
from runcoach.engine.plan import fixed_instances
from runcoach.engine.scheduler import fixed_leg_load
from runcoach.ingest.matcher import local_day
from runcoach.sync import first_record_week, weekly_leg_loads
from runcoach.web.deps import get_cfg, get_conn, render, today
from runcoach.web.stats import SportShare, WeekProgress, balance, week_progress

router = APIRouter()


@dataclass(frozen=True)
class Day:
    date: date
    sessions: list[Session]
    fixed: list[CommitmentOccurrence]
    notes: list[str]
    events: list[CommitmentOccurrence]  # drinks, holidays and trips touching this day


def build_week(
    conn: sqlite3.Connection, cfg: ConfigNode, start: date, days: int = 7
) -> tuple[list[Day], list[str]]:
    """`days` days of planned sessions, fixed commitments and events, plus warnings."""
    end = start + timedelta(days=days)
    sessions = db.active_sessions(conn, start, end)
    occurrences = expand_commitments(db.list_commitments(conn), start, end - timedelta(days=1))
    others = [o for o in occurrences if o.kind != "fixed"]
    constraints = day_constraints(others, [], cfg)
    result = [
        Day(
            date=day,
            sessions=[s for s in sessions if s.date == day],
            fixed=[o for o in occurrences if o.kind == "fixed" and o.date == day],
            notes=[constraints[day].reason] if day in constraints else [],
            events=[o for o in others if o.date == day],
        )
        for day in (start + timedelta(days=offset) for offset in range(days))
    ]
    return result, [] if db.active_plan(conn) else ["No active plan yet."]


def week_stats(conn: sqlite3.Connection, start: date) -> WeekProgress:
    """Sessions done and run km against plan for the week starting `start`."""
    end = start + timedelta(days=7)
    sessions = db.active_sessions(conn, start, end)
    done = [a for a in db.activities(conn) if start <= local_day(a.started_at) < end]
    return week_progress(sessions, done)


@dataclass(frozen=True)
class Balance:
    parts: list[SportShare]
    recent_average: float | None  # leg load of a usual recent week, for comparison
    band: tuple[float, float]  # the safe range of this week against a usual one

    @property
    def total(self) -> float:
        return sum(p.done + p.ahead for p in self.parts)


def week_balance(conn: sqlite3.Connection, cfg: ConfigNode, start: date, now: date) -> Balance:
    """The week's leg load by sport (done, and still planned from `now`) and a usual week's."""
    typed = load_scheduling_config(cfg)
    end = start + timedelta(days=7)
    activities = db.activities(conn)
    done_days = {(local_day(a.started_at), a.sport) for a in activities}
    done = list(weekly_leg_loads(activities, [start], typed.load_factors)[start].items())
    sessions = db.active_sessions(conn, start, end)
    fixed, _ = fixed_instances(db.list_commitments(conn), max(start, now), end - timedelta(days=1))
    ahead: list[tuple[str, float]] = [
        (s.sport, s.leg_load) for s in sessions if s.status == "planned" and s.date >= now
    ]
    ahead += [
        (f.sport, fixed_leg_load(f, typed))
        for f in fixed
        if (f.date, f.sport) not in done_days  # already played: it is in `done`
    ]
    # A usual week: the average of the recent complete weeks since your records began.
    first = first_record_week(activities, start)
    weeks = [start - timedelta(weeks=n) for n in range(typed.continuous.chronic_load_weeks, 0, -1)]
    loads = weekly_leg_loads(activities, [w for w in weeks if w >= first], typed.load_factors)
    known = [sum(v.values()) for v in loads.values()]
    return Balance(
        balance(done, ahead),
        sum(known) / len(known) if known else None,
        (typed.continuous.acr_low, typed.continuous.acr_high),
    )


@router.get("/week", response_class=HTMLResponse)
def week(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
    start: date | None = None,
) -> HTMLResponse:
    """Seven days of sessions with their reasons, fixed commitments and events."""
    current = today()
    week_start = monday(start or current)
    days, warnings = build_week(conn, cfg, week_start)
    return render(
        request,
        "week.html",
        {
            "active": "week",
            "days": days,
            "warnings": warnings,
            "today": current,
            "week_start": week_start,
            "progress": week_stats(conn, week_start),
            "balance": week_balance(conn, cfg, week_start, current),
            "previous": week_start - timedelta(days=7),
            "next": week_start + timedelta(days=7),
        },
    )
