from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from runcoach import db
from runcoach.config import ConfigNode, load_scheduling_config
from runcoach.domain.models import Activity, Session
from runcoach.ingest.inbox import PARSERS, ingest_file
from runcoach.ingest.matcher import local_day
from runcoach.sync import adapt_plan, match_activities
from runcoach.web.deps import get_cfg, get_conn, redirect, render, today

router = APIRouter(prefix="/activities")
_ASSIGN_WINDOW = timedelta(days=3)  # how far either side of an activity to offer sessions


def _candidates(activity: Activity, sessions: list[Session]) -> list[Session]:
    day = local_day(activity.started_at)
    return [
        s
        for s in sessions
        if s.status in {"planned", "skipped"}
        and s.sport == activity.sport
        and abs(s.date - day) <= _ASSIGN_WINDOW
    ]


def _page(
    request: Request, conn: sqlite3.Connection, errors: list[str], status_code: int = 200
) -> HTMLResponse:
    plan = db.active_plan(conn)
    sessions = db.plan_sessions(conn, plan.id) if plan and plan.id else []
    activities = db.activities(conn)
    unmatched = [a for a in activities if a.session_id is None]
    return render(
        request,
        "activities.html",
        {
            "active": "activities",
            "errors": errors,
            "unmatched": [(a, _candidates(a, sessions)) for a in reversed(unmatched)],
            "recent": [a for a in reversed(activities) if a.session_id is not None][:20],
            "accept": ",".join(PARSERS),
        },
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
def activities(
    request: Request, conn: Annotated[sqlite3.Connection, Depends(get_conn)]
) -> HTMLResponse:
    """Upload a run, and assign activities the matcher could not place."""
    return _page(request, conn, [])


@router.post("/upload", response_model=None)
async def upload(
    request: Request,
    file: Annotated[UploadFile, File()],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> Response:
    """Store an activity file, then match it and re-run the rules straight away."""
    typed = load_scheduling_config(cfg)
    try:
        ingest_file(conn, await file.read(), file.filename or "upload", typed)
    except ValueError as exc:
        return _page(request, conn, [str(exc)], status_code=400)
    match_activities(conn, typed)
    adapt_plan(conn, typed, today())
    return redirect(request, "/activities")


@router.post("/{activity_id}/assign", response_model=None)
def assign(
    request: Request,
    activity_id: int,
    session_id: Annotated[int, Form()],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
) -> Response:
    """Link an activity to the session the human says it was."""
    if db.get_session(conn, session_id) is None:
        return _page(request, conn, ["That session no longer exists."], status_code=400)
    db.link_activity(
        conn, activity_id, session_id, rule="manual_match", reason="Assigned in the app."
    )
    return redirect(request, "/activities")
