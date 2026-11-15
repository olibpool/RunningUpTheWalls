from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from runcoach import db
from runcoach.adapt.rules import Change
from runcoach.config import ConfigNode, load_scheduling_config
from runcoach.domain.models import Activity, PlannedSession, Session
from runcoach.engine.events import day_constraints, expand_commitments
from runcoach.engine.plan import fixed_instances
from runcoach.engine.safety import check_plan
from runcoach.ingest.calendar import to_busy
from runcoach.web.deps import TIMEZONE, get_cfg, get_conn, is_htmx, parse_form, redirect, render

router = APIRouter(prefix="/session")


class DoneForm(BaseModel):
    rpe: float | None = Field(default=None, ge=1, le=10)
    minutes: int | None = Field(default=None, gt=0)


def _load(conn: sqlite3.Connection, session_id: int) -> Session:
    session = db.get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _after_action(request: Request, conn: sqlite3.Connection, session_id: int) -> Response:
    if is_htmx(request):
        return render(request, "_session_card.html", {"s": _load(conn, session_id)})
    return redirect(request, f"/session/{session_id}")


@router.get("/{session_id}", response_class=HTMLResponse)
def detail(
    request: Request, session_id: int, conn: Annotated[sqlite3.Connection, Depends(get_conn)]
) -> HTMLResponse:
    """Structured steps, the reason for the session and its adjustment history."""
    return _detail(request, conn, _load(conn, session_id))


def _detail(
    request: Request,
    conn: sqlite3.Connection,
    session: Session,
    *,
    errors: list[str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render(
        request,
        "session.html",
        {
            "active": "week",
            "s": session,
            "history": db.session_adjustments(conn, session.id),
            "errors": errors or [],
        },
        status_code=status_code,
    )


@router.post("/{session_id}/done")
async def done(
    request: Request, session_id: int, conn: Annotated[sqlite3.Connection, Depends(get_conn)]
) -> Response:
    """Mark a session done, logging a manual activity with optional RPE and minutes."""
    session = _load(conn, session_id)
    form, errors = parse_form(DoneForm, await request.form())
    if form is None:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    db.apply_changes(
        conn,
        [Change(session_id, "modify", {"status": "done"}, "manual_done", "Marked done.")],
    )
    local_start = datetime.combine(
        session.date, time.fromisoformat(session.start_time or "00:00"), tzinfo=TIMEZONE
    )
    minutes = form.minutes or session.duration_min or 0
    db.insert_activity(
        conn,
        Activity(
            source="manual",
            sport=session.sport,
            started_at=local_start.astimezone(timezone.utc).isoformat(),
            duration_s=minutes * 60,
            distance_m=None if form.minutes else session.distance_m,
            rpe=form.rpe or session.rpe,
            session_id=session_id,
        ),
    )
    return _after_action(request, conn, session_id)


@router.post("/{session_id}/skip")
def skip(
    request: Request, session_id: int, conn: Annotated[sqlite3.Connection, Depends(get_conn)]
) -> Response:
    """Mark a session skipped; it is never crammed into another day."""
    _load(conn, session_id)
    db.apply_changes(
        conn,
        [Change(session_id, "modify", {"status": "skipped"}, "manual_skip", "Skipped.")],
    )
    return _after_action(request, conn, session_id)


class MoveForm(BaseModel):
    date: date
    start_time: str | None = None


@router.post("/{session_id}/move", response_model=None)
async def move(
    request: Request,
    session_id: int,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> Response:
    """Move a session; refuse with the reasons when the move breaks a safety rule."""
    session = _load(conn, session_id)
    form, errors = parse_form(MoveForm, await request.form())
    if form is None:
        return _detail(request, conn, session, errors=errors, status_code=400)
    start_time = form.start_time or session.start_time
    moved = session.model_copy(update={"date": form.date, "start_time": start_time})
    typed = load_scheduling_config(cfg)
    neighbours = [
        s
        for s in db.plan_sessions(conn, session.plan_id)
        if s.status in {"planned", "done"} and abs(s.date - form.date) <= timedelta(days=14)
    ]
    window = (form.date - timedelta(days=14), form.date + timedelta(days=14))
    commitments = db.list_commitments(conn)
    occurrences = expand_commitments(commitments, *window)
    fixed = fixed_instances(commitments, *window)[0]
    limits = day_constraints([o for o in occurrences if o.kind != "fixed"], [], typed)
    busy = to_busy(db.calendar_events(conn))

    def problems(sessions: list[PlannedSession]) -> set[str]:
        found = check_plan(sessions, typed, fixed=fixed, busy=busy, day_constraints=limits)
        return {v.message for v in found}

    # Only problems the move itself introduces block it.
    others: list[PlannedSession] = [s for s in neighbours if s.id != session_id]
    new = sorted(problems([*others, moved]) - problems(list[PlannedSession](neighbours)))
    if new:
        return _detail(
            request, conn, session,
            errors=["Not moved: " + message for message in new], status_code=400,
        )  # fmt: skip
    db.apply_changes(
        conn,
        [
            Change(
                session_id, "modify", {"date": form.date, "start_time": start_time}, "manual_move",
                f"Moved from {session.date:%a %d %b} to {form.date:%a %d %b}.",
            )
        ],
    )  # fmt: skip
    return redirect(request, f"/session/{session_id}")
