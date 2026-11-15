from __future__ import annotations

import sqlite3
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from runcoach import db
from runcoach.config import ConfigNode
from runcoach.display import monday
from runcoach.web.deps import get_cfg, get_conn, render, today
from runcoach.web.routes.week import build_week

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> HTMLResponse:
    """Today at a glance, the plan's headline and what needs attention."""
    current = today()
    plan = db.active_plan(conn)
    days, _ = build_week(conn, cfg, monday(current))
    upcoming = (
        [s for s in db.plan_sessions(conn, plan.id, current) if s.status == "planned"]
        if plan and plan.id
        else []
    )
    goal = date.fromisoformat(plan.goal_date) if plan and plan.goal_date else None
    return render(
        request,
        "home.html",
        {
            "active": "home",
            "day": next(d for d in days if d.date == current),
            "today": current,
            "plan": plan,
            "weeks_to_go": max((goal - current).days, 0) // 7 if goal else None,
            "next_session": next((s for s in upcoming if s.date > current), None),
            "unmatched": len(db.activities(conn, unmatched_only=True)),
            "retest": db.get_kv(conn, "retest_suggested"),
            "has_events": bool(db.list_commitments(conn)),
            "last_sync": db.get_kv(conn, "sync.last_run"),
        },
    )


@router.get("/guide", response_class=HTMLResponse)
def guide(request: Request) -> HTMLResponse:
    """The user guide."""
    return render(request, "guide.html", {"active": "guide"})
