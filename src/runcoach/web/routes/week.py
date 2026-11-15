from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from runcoach import db
from runcoach.config import ConfigNode
from runcoach.display import monday
from runcoach.domain.models import Session
from runcoach.engine.events import CommitmentOccurrence, day_constraints, expand_commitments
from runcoach.ingest.matcher import local_day
from runcoach.web.deps import get_cfg, get_conn, render, today
from runcoach.web.stats import WeekProgress, week_progress

router = APIRouter()


@dataclass(frozen=True)
class Day:
    date: date
    sessions: list[Session]
    fixed: list[CommitmentOccurrence]
    notes: list[str]


def build_week(
    conn: sqlite3.Connection, cfg: ConfigNode, start: date
) -> tuple[list[Day], list[str]]:
    """Seven days of planned sessions, fixed commitments and event notes, plus warnings."""
    end = start + timedelta(days=7)
    plan = db.active_plan(conn)
    sessions = db.plan_sessions(conn, plan.id, start, end) if plan and plan.id else []
    occurrences = expand_commitments(db.list_commitments(conn), start, end - timedelta(days=1))
    constraints = day_constraints([o for o in occurrences if o.kind != "fixed"], [], cfg)
    days = [
        Day(
            date=day,
            sessions=[s for s in sessions if s.date == day],
            fixed=[o for o in occurrences if o.kind == "fixed" and o.date == day],
            notes=[constraints[day].reason] if day in constraints else [],
        )
        for day in (start + timedelta(days=offset) for offset in range(7))
    ]
    warnings: list[str] = []
    if plan is None:
        warnings.append("No active plan yet.")
    return days, warnings


def week_stats(conn: sqlite3.Connection, start: date) -> WeekProgress:
    """Sessions done and run km against plan for the week starting `start`."""
    plan = db.active_plan(conn)
    end = start + timedelta(days=7)
    sessions = db.plan_sessions(conn, plan.id, start, end) if plan and plan.id else []
    done = [a for a in db.activities(conn) if start <= local_day(a.started_at) < end]
    return week_progress(sessions, done)


@router.get("/week", response_class=HTMLResponse)
def week(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
    start: date | None = None,
) -> HTMLResponse:
    """Seven days of sessions with their reasons, fixed commitments and event notes."""
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
            "previous": week_start - timedelta(days=7),
            "next": week_start + timedelta(days=7),
        },
    )
