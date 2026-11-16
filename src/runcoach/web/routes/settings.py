from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, BeforeValidator, Field

from runcoach import db
from runcoach.config import ConfigNode, load_scheduling_config, save_settings, with_overrides
from runcoach.domain.models import DISTANCE_METRES, Athlete, Distance
from runcoach.engine.time_trial import apply_time_trial_result
from runcoach.integrations import garmin
from runcoach.sync import run_sync
from runcoach.web.deps import get_cfg, get_conn, is_htmx, parse_form, redirect, render
from runcoach.web.routes.plans import race_seconds

router = APIRouter()
log = logging.getLogger(__name__)


class AthleteForm(BaseModel):
    name: str | None = None
    vdot: float = Field(ge=20, le=90)
    runs_per_week: int = Field(ge=2, le=6)
    climbing_target_per_week: int = Field(ge=0, le=3)
    max_weekly_load: float | None = Field(default=None, gt=0)
    max_hr: int | None = Field(default=None, ge=120, le=230)


class TimeTrialForm(BaseModel):
    distance: Distance
    time: str


def _windows(text: str) -> list[tuple[str, str]]:
    """'06:30-08:00, 17:30-21:00' -> [('06:30', '08:00'), ('17:30', '21:00')]."""
    windows = []
    for part in filter(None, (p.strip() for p in text.split(","))):
        start, sep, end = part.partition("-")
        if not sep:
            raise ValueError(f"'{part}' is not a window like 06:30-08:00")
        times = [datetime.strptime(t.strip(), "%H:%M").strftime("%H:%M") for t in (start, end)]
        if times[0] >= times[1]:
            raise ValueError(f"'{part}' ends before it starts")
        windows.append((times[0], times[1]))
    return windows


def _words(text: str) -> list[str]:
    return [word.strip().lower() for word in text.split(",") if word.strip()]


class AppSettingsForm(BaseModel):
    weekday: Annotated[list[tuple[str, str]], BeforeValidator(_windows)] = []
    weekend: Annotated[list[tuple[str, str]], BeforeValidator(_windows)] = []
    buffer_minutes: int = Field(ge=0, le=120)
    weather_enabled: bool = False
    latitude: float = Field(default=0.0, ge=-90, le=90)
    longitude: float = Field(default=0.0, ge=-180, le=180)
    ics_url: str | None = Field(default=None, pattern=r"^https://", repr=False)
    clear_ics_url: bool = False
    keywords_fixed: Annotated[list[str], BeforeValidator(_words)] = []
    keywords_penalty: Annotated[list[str], BeforeValidator(_words)] = []
    keywords_block: Annotated[list[str], BeforeValidator(_words)] = []
    garmin_enabled: bool = False
    export_backend: Literal["none", "garmin", "fit"]

    def overrides(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "availability": {
                "weekday": self.weekday,
                "weekend": self.weekend,
                "buffer_minutes": self.buffer_minutes,
            },
            "weather": {
                "enabled": self.weather_enabled,
                "latitude": self.latitude,
                "longitude": self.longitude,
            },
            "calendar_keywords": {
                "fixed": self.keywords_fixed,
                "penalty": self.keywords_penalty,
                "block": self.keywords_block,
            },
            "garmin": {"enabled": self.garmin_enabled},
            "export": {"backend": self.export_backend},
        }
        if self.clear_ics_url or self.ics_url:  # a blank field keeps the saved address
            result["gcal"] = {"ics_url": "" if self.clear_ics_url else self.ics_url}
        return result


def _status(request: Request, conn: sqlite3.Connection) -> dict[str, Any]:
    report = db.get_kv(conn, "sync.last_report")
    return {
        "running": request.app.state.sync_lock.locked(),
        "last_run": db.get_kv(conn, "sync.last_run"),
        "report": json.loads(report) if report else {},
    }


def _page(
    request: Request,
    conn: sqlite3.Connection,
    cfg: ConfigNode,
    errors: list[str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    typed = load_scheduling_config(cfg)
    context = {
        "active": "settings",
        "errors": errors or [],
        "athlete": db.get_athlete(conn),
        "retest": db.get_kv(conn, "retest_suggested"),
        "integrations": [
            ("Google Calendar (iCal)", "configured" if typed.gcal.ics_url else "not set"),
            (
                "Weather",
                f"on ({typed.weather.latitude:.2f}, {typed.weather.longitude:.2f})"
                if typed.weather.enabled
                else "off",
            ),
            ("Activity inbox", str(Path(typed.inbox.path).expanduser())),
            (
                "Watch workouts",
                {
                    "garmin": "pushed to Garmin Connect each sync",
                    "fit": "download a .fit file from each session",
                    "none": "off",
                }[typed.export.backend],
            ),
        ],
        "typed": typed,
        "garmin_connected": garmin.logged_in(typed.garmin),
        "garmin_pending": request.app.state.garmin_pending is not None,
        **_status(request, conn),
    }
    return render(request, "settings.html", context, status_code=status_code)


@router.get("/settings", response_class=HTMLResponse)
def settings(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> HTMLResponse:
    """Athlete profile, integration status and sync."""
    return _page(request, conn, cfg)


@router.post("/settings/athlete", response_model=None)
async def save_athlete(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> Response:
    """Create or update the athlete profile."""
    form, errors = parse_form(AthleteForm, await request.form())
    if form is None:
        return _page(request, conn, cfg, errors, status_code=400)
    existing = db.get_athlete(conn)
    if existing is not None and existing.vdot == form.vdot:
        source, updated_at = existing.vdot_source, existing.vdot_updated_at
    else:
        source, updated_at = "manual", datetime.now(timezone.utc).isoformat()
    db.save_athlete(
        conn, Athlete(**form.model_dump(), vdot_source=source, vdot_updated_at=updated_at)
    )
    return redirect(request, "/settings")


@router.post("/settings/app", response_model=None)
async def save_app_settings(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> Response:
    """Save availability, calendar, weather and Garmin settings; the CLI and sync read them too."""
    form, errors = parse_form(AppSettingsForm, await request.form())
    if form is None:
        return _page(request, conn, cfg, errors, status_code=400)
    update = form.overrides()
    try:
        load_scheduling_config(with_overrides(cfg, update))
    except ValueError as exc:
        return _page(request, conn, cfg, [str(exc)], status_code=400)
    save_settings(update)
    request.app.state.cfg = with_overrides(cfg, update)
    return redirect(request, "/settings")


@router.post("/settings/time-trial", response_model=None)
async def time_trial(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> Response:
    """Update VDOT from a time trial the human has confirmed by submitting it."""
    form, errors = parse_form(TimeTrialForm, await request.form())
    athlete = db.get_athlete(conn)
    if athlete is None:
        errors = ["Save your athlete profile first."]
    if form is None or athlete is None:
        return _page(request, conn, cfg, errors, status_code=400)
    try:
        seconds = race_seconds(form.time)
    except ValueError as exc:
        return _page(request, conn, cfg, [str(exc)], status_code=400)
    updated = apply_time_trial_result(
        athlete,
        DISTANCE_METRES[form.distance],
        seconds,
        confirmed=True,
        confirmed_at=datetime.now(timezone.utc).isoformat(),
    )
    db.save_athlete(conn, updated.model_copy(update={"vdot": round(updated.vdot, 1)}))
    db.delete_kv(conn, "retest_suggested")
    return redirect(request, "/settings")


@router.post("/settings/retest/dismiss", response_model=None)
def dismiss_retest(
    request: Request, conn: Annotated[sqlite3.Connection, Depends(get_conn)]
) -> Response:
    """Hide the re-test suggestion until the rules raise it again."""
    db.delete_kv(conn, "retest_suggested")
    return redirect(request, "/settings")


def _sync_in_background(db_path: Path | str | None, cfg: ConfigNode, app_state: Any) -> None:
    try:
        conn = db.connect(db_path)
        try:
            run_sync(conn, cfg, datetime.now(timezone.utc))
        finally:
            conn.close()
    except Exception:  # the status panel shows the stored report; never crash the worker
        log.exception("background sync failed")
    finally:
        app_state.sync_lock.release()


def begin_sync(state: Any, background: BackgroundTasks | None = None) -> bool:
    """Start a sync unless one is running; True when one was started. Without `background`
    the caller runs `_sync_in_background(state.db_path, state.cfg, state)` itself."""
    if not state.sync_lock.acquire(blocking=False):
        return False
    if background is not None:
        background.add_task(_sync_in_background, state.db_path, state.cfg, state)
    return True


def sync_due(state: Any, now: datetime) -> bool:
    """True when automatic sync is on and the last sync (from here, the CLI or a timer) is
    older than `[sync] auto_minutes`."""
    minutes = load_scheduling_config(state.cfg).sync.auto_minutes
    if not minutes:
        return False
    conn = db.connect(state.db_path)
    try:
        last = db.get_kv(conn, "sync.last_run")
    finally:
        conn.close()
    return last is None or now - datetime.fromisoformat(last) >= timedelta(minutes=minutes)


def _chip(request: Request, conn: sqlite3.Connection) -> dict[str, Any]:
    status = _status(request, conn)
    last = status["last_run"]
    minutes = (
        int((datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() // 60)
        if last
        else None
    )
    failed = [step for step, outcome in status["report"].items() if outcome.startswith("error")]
    return {**status, "minutes": minutes, "failed": failed}


@router.post("/sync/run", response_model=None)
def sync_run(
    request: Request,
    background: BackgroundTasks,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
) -> Response:
    """Start a sync after the response is sent; the page polls its status."""
    begin_sync(request.app.state, background)
    if request.headers.get("HX-Target") == "sync-chip":
        return render(request, "_sync_chip.html", {**_chip(request, conn), "running": True})
    if is_htmx(request):
        return render(request, "_sync_status.html", {"running": True, "report": {}})
    return redirect(request, "/settings")


@router.get("/sync/status", response_class=HTMLResponse)
def sync_status(
    request: Request, conn: Annotated[sqlite3.Connection, Depends(get_conn)]
) -> HTMLResponse:
    """The sync status panel; it keeps polling while a sync runs."""
    return render(request, "_sync_status.html", _status(request, conn))


@router.get("/sync/chip", response_class=HTMLResponse)
def sync_chip(
    request: Request,
    background: BackgroundTasks,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    was: str | None = None,
) -> HTMLResponse:
    """The header's sync chip. Opening the app after a while starts a sync; when a sync the
    page watched finishes, the page is told to refresh."""
    state = request.app.state
    if sync_due(state, datetime.now(timezone.utc)):
        begin_sync(state, background)
    context = _chip(request, conn)
    context["running"] = state.sync_lock.locked()
    response = render(request, "_sync_chip.html", context)
    if was == "running" and not context["running"]:
        response.headers["HX-Trigger"] = json.dumps({"synced": "Synced"})
    return response


class GarminLoginForm(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1, repr=False)


class GarminCodeForm(BaseModel):
    code: str = Field(pattern=r"^\s*\d{4,8}\s*$")


def _garmin_connected(request: Request, background: BackgroundTasks, cfg: ConfigNode) -> None:
    """Turn the Garmin pull on and fetch everything straight away."""
    update = {"garmin": {"enabled": True}}
    save_settings(update)
    request.app.state.cfg = with_overrides(cfg, update)
    begin_sync(request.app.state, background)


@router.post("/garmin/login", response_model=None)
async def garmin_login(
    request: Request,
    background: BackgroundTasks,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> Response:
    """Log in to Garmin Connect; only the session token is kept, never the password."""
    form, _ = parse_form(GarminLoginForm, await request.form())
    if form is None:
        return _page(request, conn, cfg, ["Enter your Garmin email and password."], 400)
    garmin_cfg = load_scheduling_config(cfg).garmin
    try:
        pending = await run_in_threadpool(garmin.start_login, garmin_cfg, form.email, form.password)
    except Exception as exc:
        return _page(request, conn, cfg, [garmin.login_problem(exc)], 400)
    request.app.state.garmin_pending = pending
    if pending is None:
        _garmin_connected(request, background, cfg)
    return redirect(request, "/settings#garmin-title")


@router.post("/garmin/code", response_model=None)
async def garmin_code(
    request: Request,
    background: BackgroundTasks,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> Response:
    """Finish a Garmin login with the one-time code Garmin sent."""
    form, _ = parse_form(GarminCodeForm, await request.form())
    pending = request.app.state.garmin_pending
    if pending is None:
        return _page(request, conn, cfg, ["That login has expired; start again."], 400)
    if form is None:
        return _page(request, conn, cfg, ["Enter the code Garmin sent you."], 400)
    garmin_cfg = load_scheduling_config(cfg).garmin
    try:
        await run_in_threadpool(garmin.finish_login, garmin_cfg, pending, form.code.strip())
    except Exception as exc:
        return _page(request, conn, cfg, [garmin.login_problem(exc)], 400)
    request.app.state.garmin_pending = None
    _garmin_connected(request, background, cfg)
    return redirect(request, "/settings#garmin-title")
