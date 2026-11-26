from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from runcoach import db
from runcoach.adapt.rules import Change
from runcoach.config import ConfigNode, load_scheduling_config
from runcoach.domain.models import Checkin
from runcoach.engine.readiness import resting_heart_rate_hint, sleep_hint
from runcoach.sync import adapt_plan
from runcoach.web.deps import get_cfg, get_conn, is_htmx, parse_form, redirect, render, today

router = APIRouter(prefix="/checkin")


class CheckinForm(BaseModel):
    feel: int | None = Field(default=None, ge=1, le=5)
    niggle: str | None = None
    ill: bool = False
    fever: bool = False
    resting_hr: int | None = Field(default=None, gt=20, lt=150)
    sleep_hours: float | None = Field(default=None, ge=0, le=16, multiple_of=0.25)
    notes: str | None = None


def _page(
    request: Request,
    conn: sqlite3.Connection,
    cfg: ConfigNode,
    *,
    errors: list[str] | None = None,
    changes: list[Change] | None = None,
    saved: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    day = today()
    typed = load_scheduling_config(cfg)
    recent = db.checkins(conn, day - timedelta(days=typed.readiness.rhr_window_days - 1))
    current = next((c for c in recent if c.date == day), None)
    hint = resting_heart_rate_hint(recent, day, typed)
    sleep = sleep_hint(recent, day, typed)
    context: dict[str, Any] = {
        "active": "home",
        "errors": errors or [],
        "today": day,
        "current": current,
        "hint": hint,
        "sleep": sleep,
        "sleep_cfg": typed.readiness,
        "changes": changes,
        "saved": saved,
        "illness": typed.illness,
    }
    return render(request, "checkin.html", context, status_code=status_code)


@router.get("", response_class=HTMLResponse)
def checkin(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> HTMLResponse:
    """Today's check-in form, prefilled if already done."""
    return _page(request, conn, cfg)


class FeelForm(BaseModel):
    feel: int = Field(ge=1, le=5)


@router.post("/feel", response_model=None)
async def feel(
    request: Request, conn: Annotated[sqlite3.Connection, Depends(get_conn)]
) -> Response:
    """One tap from Today: how you feel, kept with anything Garmin or you already logged."""
    form, errors = parse_form(FeelForm, await request.form())
    if form is None:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    day = today()
    current = next((c for c in db.checkins(conn, day) if c.date == day), Checkin(date=day))
    saved = current.model_copy(update={"feel": form.feel})
    db.save_checkin(conn, saved)
    if not is_htmx(request):
        return redirect(request, "/")
    return render(request, "_feel.html", {"checkin": saved, "today": day, "just_saved": True})


@router.post("", response_class=HTMLResponse)
async def save(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> HTMLResponse:
    """Store the check-in, run the adaptation rules and list what changed."""
    raw = await request.form()
    form, errors = parse_form(CheckinForm, raw)
    if form is None:
        return _page(request, conn, cfg, errors=errors, status_code=400)
    db.save_checkin(conn, Checkin(date=today(), **form.model_dump()))
    changes = adapt_plan(conn, load_scheduling_config(cfg), today())
    return _page(request, conn, cfg, changes=changes, saved=True)
