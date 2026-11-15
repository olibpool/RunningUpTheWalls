from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from runcoach import db
from runcoach.config import ConfigNode, load_scheduling_config
from runcoach.display import monday
from runcoach.engine.zones import zones_from_vdot
from runcoach.web import stats
from runcoach.web.deps import get_cfg, get_conn, render, today
from runcoach.web.routes.week import build_week, week_stats

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> HTMLResponse:
    """Today at a glance, the plan's headline and what needs attention."""
    current = today()
    typed = load_scheduling_config(cfg)
    plan = db.active_plan(conn)
    days, _ = build_week(conn, cfg, monday(current))
    sessions = db.plan_sessions(conn, plan.id) if plan and plan.id else []
    upcoming = [s for s in sessions if s.status == "planned" and s.date > current]
    goal = date.fromisoformat(plan.goal_date) if plan and plan.goal_date else None
    plan_start = date.fromisoformat(plan.start_date) if plan else None
    activities = db.activities(conn)
    weeks = stats.weekly(activities, monday(current), 12)
    athlete = db.get_athlete(conn)
    resting = [c for c in db.checkins(conn, current - timedelta(days=27)) if c.resting_hr]
    return render(
        request,
        "home.html",
        {
            "active": "home",
            "day": next(d for d in days if d.date == current),
            "today": current,
            "plan": plan,
            "days_to_go": max((goal - current).days, 0) if goal else None,
            "weeks_to_go": max((goal - current).days, 0) // 7 if goal else None,
            "plan_share": (
                min(max((current - plan_start).days / ((goal - plan_start).days or 1), 0), 1)
                if goal and plan_start
                else None
            ),
            "next_session": upcoming[0] if upcoming else None,
            "progress": week_stats(conn, monday(current)),
            "weeks": weeks,
            "ratio": stats.load_ratio(weeks, typed.continuous.chronic_load_weeks),
            "ratio_band": (typed.continuous.acr_low, typed.continuous.acr_high),
            "recent": stats.recent(activities, current, 30),
            "completion": stats.completion(sessions, current),
            "athlete": athlete,
            "zones": zones_from_vdot(athlete.vdot, typed.zones) if athlete else None,
            "resting": [(c.date, c.resting_hr) for c in resting],
            "unmatched": sum(a.session_id is None for a in activities),
            "retest": db.get_kv(conn, "retest_suggested"),
            "has_events": bool(db.list_commitments(conn)),
            "last_sync": db.get_kv(conn, "sync.last_run"),
        },
    )


@router.get("/guide", response_class=HTMLResponse)
def guide(request: Request) -> HTMLResponse:
    """The user guide."""
    return render(request, "guide.html", {"active": "guide"})
