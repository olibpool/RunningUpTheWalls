from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from runcoach import db
from runcoach.config import ConfigNode, load_scheduling_config
from runcoach.domain.models import Commitment, commitment_problem
from runcoach.ingest.calendar import detect_commitments
from runcoach.sync import adapt_plan
from runcoach.web.deps import get_cfg, get_conn, parse_form, redirect, render, today

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
    return render(
        request,
        "events.html",
        {
            "active": "events",
            "fixed": [c for c in commitments if c.kind == "fixed"],
            "events": [c for c in commitments if c.kind != "fixed"],
            "proposals": _proposals(conn, cfg),
            "errors": errors,
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
                adapt_plan(conn, load_scheduling_config(cfg), today())  # apply it straight away
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


@router.post("/{commitment_id}/delete", response_model=None)
def delete(
    request: Request, commitment_id: int, conn: Annotated[sqlite3.Connection, Depends(get_conn)]
) -> Response:
    """Remove a commitment or event."""
    db.delete_commitment(conn, commitment_id)
    return redirect(request, "/events")
