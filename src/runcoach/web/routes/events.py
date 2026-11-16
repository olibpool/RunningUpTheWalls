from __future__ import annotations

import sqlite3
from datetime import timedelta
from statistics import median
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from runcoach import db
from runcoach.adapt.rules import Change
from runcoach.config import ConfigNode, load_scheduling_config
from runcoach.domain.models import Commitment, Session, commitment_problem
from runcoach.ingest.calendar import detect_commitments
from runcoach.ingest.matcher import local_day
from runcoach.sync import adapt_plan
from runcoach.web.deps import get_cfg, get_conn, is_htmx, parse_form, redirect, render, today

router = APIRouter(prefix="/events")


class EventForm(BaseModel):
    """One form shape for every quick log; `form` says which one was submitted."""

    form: Literal["fixed", "drinks", "holiday"]
    title: str | None = None
    sport: str | None = None
    weekday: int | None = None
    start_time: str | None = None
    duration_min: int | None = Field(default=None, gt=0)
    rpe: float | None = Field(default=None, ge=1, le=10)
    date: str | None = None
    severity: int | None = Field(default=None, ge=1, le=3)
    start_date: str | None = None
    end_date: str | None = None
    holiday_type: Literal["break", "travel", "active"] = "break"

    def commitment(self) -> Commitment:
        """Map the submitted quick log onto the commitment table's event kinds."""
        if self.form == "fixed":
            return Commitment(
                kind="fixed",
                title=self.title or (self.sport or "Fixed").title(),
                sport=self.sport,
                weekday=self.weekday,
                start_time=self.start_time,
                duration_min=self.duration_min,
                rpe=self.rpe,
            )
        if self.form == "drinks":
            return Commitment(
                kind="penalty",
                title=self.title or "Drinks",
                start_date=self.date,
                end_date=self.date,
                severity=self.severity,
            )
        return Commitment(
            kind="credit" if self.holiday_type == "active" else "block",
            title=self.title or "Holiday",
            start_date=self.start_date,
            end_date=self.end_date,
            minimal_plan=self.holiday_type == "travel",
            rpe=self.rpe if self.holiday_type == "active" else None,
            duration_min=self.duration_min if self.holiday_type == "active" else None,
        )


def describe(conn: sqlite3.Connection, changes: list[Change]) -> list[tuple[Session | None, str]]:
    """Each plan change with the session it touched, for a reply the human can read."""
    return [
        (db.get_session(conn, c.session_id) if c.session_id else None, c.reason) for c in changes
    ]


def _proposals(conn: sqlite3.Connection, cfg: ConfigNode) -> list[Commitment]:
    """Calendar events that look like commitments, minus any already confirmed."""
    known = {c.external_id for c in db.list_commitments(conn) if c.external_id}
    keywords = load_scheduling_config(cfg).calendar_keywords
    return [
        p
        for p in detect_commitments(db.calendar_events(conn), keywords)
        if p.external_id not in known
    ]


def _page(
    request: Request,
    conn: sqlite3.Connection,
    cfg: ConfigNode,
    errors: list[str],
    status_code: int = 200,
) -> HTMLResponse:
    commitments = db.list_commitments(conn)
    day = today().isoformat()
    events = sorted((c for c in commitments if c.kind != "fixed"), key=lambda c: c.start_date or "")
    typed = load_scheduling_config(cfg)
    # How long each sport really lasts lately, to catch a commitment entered as longer or shorter.
    since = today() - timedelta(days=typed.progress.typical_window_days)
    minutes: dict[str, list[float]] = {}
    for a in db.activities(conn):
        if local_day(a.started_at) >= since:
            minutes.setdefault(a.sport, []).append(a.duration_s / 60)
    typical = {
        sport: round(median(values))
        for sport, values in minutes.items()
        if len(values) >= typed.progress.typical_min_samples
    }
    return render(
        request,
        "events.html",
        {
            "active": "events",
            "fixed": [c for c in commitments if c.kind == "fixed"],
            "upcoming": [c for c in events if (c.end_date or c.start_date or "") >= day],
            "past": [c for c in events if (c.end_date or c.start_date or "") < day][::-1],
            "proposals": _proposals(conn, cfg),
            "errors": errors,
            "today": today(),
            "days_after": typed.penalty.days_after,
            "ramp": typed.return_ramp,
            "athlete": db.get_athlete(conn),
            "typical": typical,
            "tolerance": typed.progress.typical_tolerance,
        },
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
def events(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> HTMLResponse:
    """Fixed commitments, drinks nights and holidays, with forms to add more."""
    return _page(request, conn, cfg, [])


@router.post("", response_model=None)
async def add(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> Response:
    """Validate and store one commitment or event."""
    form, errors = parse_form(EventForm, await request.form())
    if form is not None:
        try:
            commitment = form.commitment()
        except ValueError as exc:
            errors = [str(exc)]
        else:
            problem = commitment_problem(commitment)
            if problem is None:
                db.save_commitment(conn, commitment)
                # Applied straight away; a quick log from Today gets the coach's reply in place.
                changes = adapt_plan(conn, load_scheduling_config(cfg), today())
                if is_htmx(request) and "reply" in (await request.form()):
                    return render(
                        request, "_coach_reply.html", {"changes": describe(conn, changes)}
                    )
                return redirect(request, "/events")
            errors = [problem]
    return _page(request, conn, cfg, errors, status_code=400)


@router.post("/proposals/confirm", response_model=None)
def confirm(
    request: Request,
    external_id: Annotated[str, Form()],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> Response:
    """Turn a calendar proposal into an active commitment, as detected (not as posted)."""
    proposal = next((p for p in _proposals(conn, cfg) if p.external_id == external_id), None)
    if proposal is None:
        return _page(request, conn, cfg, ["That proposal is no longer available."], 400)
    if proposal.kind == "penalty":
        proposal = proposal.model_copy(update={"severity": 2})  # the human can delete and re-log
    db.save_commitment(conn, proposal.model_copy(update={"active": True}))
    adapt_plan(conn, load_scheduling_config(cfg), today())
    return redirect(request, "/events")


class DurationForm(BaseModel):
    minutes: int = Field(gt=0, le=600)


@router.post("/{commitment_id}/duration", response_model=None)
async def duration(
    request: Request,
    commitment_id: int,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> Response:
    """Set a commitment's length, e.g. to what the watch records."""
    form, errors = parse_form(DurationForm, await request.form())
    if form is None:
        return _page(request, conn, cfg, errors, status_code=400)
    try:
        db.set_commitment_duration(conn, commitment_id, form.minutes)
    except KeyError:
        return _page(request, conn, cfg, ["That commitment no longer exists."], 400)
    return redirect(request, "/events")


@router.post("/{commitment_id}/delete", response_model=None)
def delete(
    request: Request, commitment_id: int, conn: Annotated[sqlite3.Connection, Depends(get_conn)]
) -> Response:
    """Remove a commitment or event."""
    db.delete_commitment(conn, commitment_id)
    return redirect(request, "/events")
