"""Shared request dependencies and rendering helpers for the web routes."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError
from starlette.datastructures import FormData

from runcoach import db, display
from runcoach.config import ConfigNode

TIMEZONE = ZoneInfo("Europe/London")  # planned sessions are local dates (working rule 8)

# One entry per top-level page; add new pages here so the navigation stays in one place.
NAV = [("week", "Week", "/week"), ("plan", "Plan", "/plan"), ("events", "Events", "/events")]

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters.update(
    pace=display.pace,
    step_pace=display.step_pace,
    main_pace=display.main_pace,
    structure=display.structure,
    clock=display.clock,
    km=display.km,
)
templates.env.globals["nav"] = NAV


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """Open one database connection per request."""
    conn = db.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_cfg(request: Request) -> ConfigNode:
    """Return the config loaded when the app started."""
    cfg: ConfigNode = request.app.state.cfg
    return cfg


def today() -> date:
    """Today's local date in the athlete's timezone."""
    return datetime.now(TIMEZONE).date()


def is_htmx(request: Request) -> bool:
    """True when htmx made the request and expects a fragment."""
    return request.headers.get("HX-Request") == "true"


def render(
    request: Request, name: str, context: dict[str, Any], status_code: int = 200
) -> HTMLResponse:
    """Render a template with the request in its context."""
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def redirect(request: Request, url: str) -> Response:
    """Redirect after a POST, in a way both htmx and plain forms follow."""
    if is_htmx(request):
        return Response(status_code=204, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def parse_form[M: BaseModel](model: type[M], form: FormData) -> tuple[M | None, list[str]]:
    """Validate form fields into a model; blank fields count as missing."""
    data = {key: value for key, value in form.items() if value != ""}
    try:
        return model.model_validate(data), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(part) for part in error['loc']) or 'form'}: {error['msg']}"
            for error in exc.errors()
        ]
