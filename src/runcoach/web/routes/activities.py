from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from runcoach import db
from runcoach.config import ConfigNode, load_scheduling_config
from runcoach.display import monday
from runcoach.domain.models import Activity, Session
from runcoach.ingest.inbox import PARSERS, ingest_file
from runcoach.ingest.matcher import local_day
from runcoach.sync import adapt_plan, match_activities
from runcoach.web.deps import get_cfg, get_conn, is_htmx, parse_form, redirect, render, today

router = APIRouter(prefix="/activities")
_ASSIGN_WINDOW = timedelta(days=3)  # how far either side of an activity to offer sessions
_RATE_WINDOW = timedelta(days=7)  # how far back to ask how hard an activity felt


_LOG_WEEKS = 12  # shortcut: no paging; older weeks are on Progress as totals


@dataclass(frozen=True)
class LogEntry:
    activity: Activity
    session: Session | None
    candidates: list[Session]
    unrated: bool


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
    sessions = db.active_sessions(conn)
    by_id = {s.id: s for s in sessions}
    unrated = {
        a.id for a in db.unrated_activities(conn, datetime.now(timezone.utc) - _RATE_WINDOW, 10)
    }
    weeks: dict[date, list[LogEntry]] = {}
    for a in reversed(db.activities(conn)):
        entry = LogEntry(
            a, by_id.get(a.session_id) if a.session_id else None,
            [] if a.session_id else _candidates(a, sessions), a.id in unrated,
        )  # fmt: skip
        weeks.setdefault(monday(local_day(a.started_at)), []).append(entry)
    return render(
        request,
        "activities.html",
        {
            "active": "activities",
            "errors": errors,
            "weeks": list(weeks.items())[:_LOG_WEEKS],
            "unrated": [e.activity for w in weeks.values() for e in w if e.unrated],
            "accept": ",".join(PARSERS),
            "plan_start": date.fromisoformat(plan.start_date) if plan else None,
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


@router.post("/{activity_id}/unassign", response_model=None)
def unassign(
    request: Request,
    activity_id: int,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> Response:
    """The matcher got it wrong: free the session, then match again (a same-day entry you
    logged by hand is merged with it)."""
    try:
        db.unlink_activity(conn, activity_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Activity not found") from None
    match_activities(conn, load_scheduling_config(cfg))
    return redirect(request, "/activities")


class RpeForm(BaseModel):
    rpe: float = Field(ge=1, le=10, multiple_of=0.5)


@router.post("/{activity_id}/rpe", response_model=None)
async def rate(
    request: Request,
    activity_id: int,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> Response:
    """Record how hard an activity felt, then re-run the rules with its real load."""
    form, errors = parse_form(RpeForm, await request.form())
    if form is None:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    try:
        db.set_activity_rpe(conn, activity_id, form.rpe)
    except KeyError:
        raise HTTPException(status_code=404, detail="Activity not found") from None
    adapt_plan(conn, load_scheduling_config(cfg), today())
    if not is_htmx(request):
        return redirect(request, "/")
    return Response(
        "", media_type="text/html", headers={"HX-Trigger": '{"toast": "Effort saved."}'}
    )
