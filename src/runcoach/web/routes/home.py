from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from runcoach import db
from runcoach.config import ConfigNode, load_scheduling_config
from runcoach.display import monday, phase
from runcoach.engine.readiness import resting_heart_rate_hint, sleep_hint
from runcoach.integrations import garmin
from runcoach.web import stats
from runcoach.web.deps import get_cfg, get_conn, render, today
from runcoach.web.routes.week import build_week, week_balance, week_stats

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> HTMLResponse:
    """Today: what to do, how you are, what's coming and how the week balances."""
    current = today()
    typed = load_scheduling_config(cfg)
    plan = db.active_plan(conn)
    days, _ = build_week(conn, cfg, current, typed.progress.upcoming_days + 1)
    sessions = db.active_sessions(conn)
    goal = date.fromisoformat(plan.goal_date) if plan and plan.goal_date else None
    plan_start = date.fromisoformat(plan.start_date) if plan else None
    this_week = [s for s in sessions if monday(s.date) == monday(current)]
    activities = db.activities(conn)
    recent_checkins = db.checkins(conn, current - timedelta(days=27))
    return render(
        request,
        "home.html",
        {
            "active": "home",
            "today": current,
            "day": days[0],
            "upcoming": days[1:],
            "plan": plan,
            "phase": next((p for s in this_week if (p := phase(s.reason))), None),
            "plan_week": (monday(current) - monday(plan_start)).days // 7 + 1
            if plan_start
            else None,
            "plan_weeks": (monday(goal) - monday(plan_start)).days // 7 + 1
            if goal and plan_start
            else None,
            "days_to_go": max((goal - current).days, 0) if goal else None,
            "progress": week_stats(conn, monday(current)),
            "balance": week_balance(conn, cfg, monday(current), current),
            "athlete": db.get_athlete(conn),
            "checkin": next((c for c in recent_checkins if c.date == current), None),
            "hint": resting_heart_rate_hint(recent_checkins, current, typed),
            "sleep": sleep_hint(recent_checkins, current, typed),
            "sleep_cfg": typed.readiness,
            "streak": stats.streak(activities, current, typed.progress.streak_per_week),
            "streak_per_week": typed.progress.streak_per_week,
            "recent": stats.recent(activities, current, 30),
            "unrated": db.unrated_activities(
                conn, datetime.now(timezone.utc) - timedelta(days=7), 5
            ),
            "retest": db.get_kv(conn, "retest_suggested"),
            "has_events": bool(db.list_commitments(conn)),
            "garmin_connected": garmin.logged_in(typed.garmin),
        },
    )


@router.get("/guide", response_class=HTMLResponse)
def guide(request: Request) -> HTMLResponse:
    """The user guide."""
    return render(request, "guide.html", {"active": "guide"})
