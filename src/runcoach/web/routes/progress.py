from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from runcoach import db
from runcoach.config import ConfigNode, load_scheduling_config
from runcoach.display import monday
from runcoach.engine.fitness import Run, fitness_trend
from runcoach.engine.zones import zones_from_vdot
from runcoach.ingest.matcher import local_day
from runcoach.sync import weekly_leg_loads
from runcoach.web import stats
from runcoach.web.deps import get_cfg, get_conn, render, today

router = APIRouter()


@router.get("/progress", response_class=HTMLResponse)
def progress(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> HTMLResponse:
    """Streaks, consistency, load by sport, fitness, records and paces."""
    current = today()
    typed = load_scheduling_config(cfg)
    activities = db.activities(conn)
    athlete = db.get_athlete(conn)
    sessions = db.active_sessions(conn)
    this_monday = monday(current)

    weeks = [this_monday - timedelta(weeks=n) for n in range(11, -1, -1)]
    by_week = weekly_leg_loads(activities, weeks, typed.load_factors)

    checkins = db.checkins(conn, current - timedelta(weeks=typed.fitness.trend_weeks + 6))
    runs = [
        Run(local_day(a.started_at), a.duration_s / 60, a.distance_m or 0, a.avg_hr)
        for a in activities
        if a.sport == "run" and a.avg_hr and a.distance_m
    ]
    trend = (
        fitness_trend(
            runs,
            {c.date: c.resting_hr for c in checkins if c.resting_hr},
            athlete.max_hr,
            current,
            typed.fitness,
        )  # fmt: skip
        if athlete and athlete.max_hr
        else []
    )
    recent_checkins = [c for c in checkins if c.date > current - timedelta(days=28)]
    return render(
        request,
        "progress.html",
        {
            "active": "progress",
            "today": current,
            "athlete": athlete,
            "streak": stats.streak(activities, current, typed.progress.streak_per_week),
            "streak_per_week": typed.progress.streak_per_week,
            "grid": stats.activity_grid(activities, current, typed.progress.grid_weeks),
            "load_bars": stats.stack_bars([(w, by_week[w]) for w in weeks], this_monday, link=True),
            "km_weeks": stats.weekly(activities, this_monday, 12),
            "completion": stats.completion(sessions, current),
            "recent": stats.recent(activities, current, 30),
            "trend": trend,
            "retest_gap": typed.fitness.retest_gap_vdot,
            "records": stats.records(activities, typed.progress.fastest_min_km),
            "fastest_min_km": typed.progress.fastest_min_km,
            "zones": zones_from_vdot(athlete.vdot, typed.zones) if athlete else None,
            "resting": [(c.date, c.resting_hr) for c in recent_checkins if c.resting_hr],
            "slept": [
                (c.date, c.sleep_hours) for c in recent_checkins if c.sleep_hours is not None
            ],
        },
    )
