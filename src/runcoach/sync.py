"""`runcoach sync`: the 30-minute job. Each step runs in isolation so one failure (a network
error, a bad file) never stops the rest, and records its last success in kv."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

import httpx

from runcoach import db
from runcoach.adapt.heat import adjust_for_heat
from runcoach.adapt.reschedule import reschedule_for_calendar
from runcoach.adapt.rules import Change, PlanState, evaluate
from runcoach.config import ConfigNode, SchedulingConfig, load_scheduling_config
from runcoach.display import monday
from runcoach.domain.models import LOCAL_TIMEZONE, Athlete, Commitment
from runcoach.engine.continuous import (
    ContinuousPlanResult,
    History,
    WeekLoad,
    block_intervals,
    generate_continuous_plan_result,
)
from runcoach.engine.events import day_constraints, expand_commitments
from runcoach.engine.plan import fixed_instances
from runcoach.engine.replan import should_replan
from runcoach.ingest.calendar import fetch_ical_events, to_busy
from runcoach.ingest.inbox import ingest_inbox
from runcoach.ingest.matcher import local_day, match
from runcoach.integrations.weather import fetch_forecast

log = logging.getLogger(__name__)
TZ = ZoneInfo(LOCAL_TIMEZONE)


def replan_continuous_if_due(
    conn: sqlite3.Connection,
    plan_id: int,
    athlete: Athlete,
    history: History,
    today: date,
    now_utc: datetime,
    events: list[Commitment],
    cfg: ConfigNode,
) -> ContinuousPlanResult | None:
    """Regenerate the continuous plan's coming weeks when due; refuses unsafe plans (D4)."""
    last_replanned_at = db.get_kv(conn, "last_replanned_at")
    if not should_replan(today, now_utc, last_replanned_at, cfg):
        return None

    plan = conn.execute(
        "SELECT start_date, mode, status FROM plan WHERE id = ?",
        (plan_id,),
    ).fetchone()
    if plan is None or plan["mode"] != "continuous" or plan["status"] != "active":
        raise ValueError(f"Plan {plan_id} is not an active continuous plan")
    current_history = history
    if current_history.plan_start_date is None:
        current_history = replace(
            current_history,
            plan_start_date=date.fromisoformat(plan["start_date"]),
        )
    result = generate_continuous_plan_result(athlete, current_history, today, events, cfg)
    if result.violations:
        details = "; ".join(f"{v.kind}: {v.message}" for v in result.violations)
        raise ValueError(f"Re-plan not saved; it fails the safety checks: {details}")
    db.replace_continuous_sessions(
        conn,
        plan_id,
        result.sessions,
        today=today,
        replanned_at=now_utc.isoformat(),
    )
    return result


def _calendar(
    conn: sqlite3.Connection, cfg: SchedulingConfig, today: date, client: httpx.Client | None
) -> str:
    if not cfg.gcal.ics_url:
        return "skipped: no iCal URL configured"
    start = today - timedelta(days=cfg.gcal.days_back)
    end = today + timedelta(days=cfg.gcal.days_ahead)
    try:
        events = fetch_ical_events(
            cfg.gcal.ics_url, window_start=start, window_end=end, client=client
        )
    except httpx.HTTPError as exc:
        # The URL is a secret and httpx puts it in its messages; report only the error type.
        raise RuntimeError(f"calendar fetch failed ({type(exc).__name__})") from None
    db.cache_calendar_events(conn, events, window_start=start, window_end=end)
    digest = hashlib.sha256(
        json.dumps([(e.external_id, e.starts_at, e.ends_at) for e in events]).encode()
    ).hexdigest()
    if digest == db.get_kv(conn, "calendar.hash"):
        return f"ok: {len(events)} events, unchanged"
    moved = 0
    plan = db.active_plan(conn)
    if plan is not None and plan.id is not None:
        horizon = today + timedelta(days=cfg.gcal.reschedule_days + 7)
        commitments = db.list_commitments(conn)
        fixed, _ = fixed_instances(commitments, today - timedelta(days=7), horizon)
        occurrences = expand_commitments(commitments, today - timedelta(days=7), horizon)
        changes = reschedule_for_calendar(
            db.plan_sessions(conn, plan.id),
            fixed,
            to_busy(db.calendar_events(conn)),
            today,
            cfg,
            horizon_days=cfg.gcal.reschedule_days,
            day_constraints=day_constraints([o for o in occurrences if o.kind != "fixed"], [], cfg),
        )
        db.apply_changes(conn, changes)
        moved = len(changes)
    db.set_kv(conn, "calendar.hash", digest)
    return f"ok: {len(events)} events, {moved} sessions changed"


def _forecast(conn: sqlite3.Connection, cfg: SchedulingConfig, client: httpx.Client | None) -> str:
    if not cfg.weather.enabled:
        return "skipped: weather disabled (set [weather] in config/local.toml)"
    forecast = fetch_forecast(cfg.weather, client)
    db.set_kv(conn, "forecast", json.dumps(forecast))
    return f"ok: {len(forecast)} hours"


def match_activities(conn: sqlite3.Connection, cfg: SchedulingConfig) -> str:
    """Link unmatched activities to planned sessions; also used right after an upload."""
    plan = db.active_plan(conn)
    if plan is None or plan.id is None:
        return "skipped: no active plan"
    unmatched = db.activities(conn, unmatched_only=True)
    matches = match(unmatched, db.plan_sessions(conn, plan.id), cfg.matching)
    for m in matches:
        db.link_activity(
            conn, m.activity_id, m.session_id, rule="matched",
            reason=f"Matched to an uploaded activity (score {m.score:.2f}).",
        )  # fmt: skip
    return f"ok: {len(matches)} matched, {len(unmatched) - len(matches)} unmatched"


def adapt_plan(conn: sqlite3.Connection, cfg: SchedulingConfig, today: date) -> list[Change]:
    """Run the adaptation rules and heat check on the active plan; also used by the web app."""
    plan = db.active_plan(conn)
    if plan is None or plan.id is None:
        return []
    sessions = db.plan_sessions(conn, plan.id)
    applied = frozenset(db.applied_rules(conn))
    # Only events the plan wasn't generated with; it already accounts for the rest.
    known = set(json.loads(plan.config_snapshot or "{}").get("event_ids", []))
    new_events = [c for c in db.list_commitments(conn) if c.kind != "fixed" and c.id not in known]
    horizon = max((s.date for s in sessions), default=today)
    occurrences = expand_commitments(new_events, today - timedelta(days=7), horizon)
    state = PlanState(
        sessions=sessions,
        activities=db.activities(conn),
        checkins=db.checkins(conn, today - timedelta(days=28)),
        applied=applied,
        flags=frozenset(db.kv_keys(conn)),
        constraints=day_constraints(occurrences, [], cfg),
        # Continuous plans rebuild their own return ramp at each weekly re-plan.
        blocks=block_intervals(new_events) if plan.mode == "race" else [],
    )
    changes: list[Change] = evaluate(state, today, cfg)
    forecast_json = db.get_kv(conn, "forecast")
    if forecast_json:
        forecast: dict[str, float] = json.loads(forecast_json)
        heat = (
            adjust_for_heat(s, forecast, today, cfg)
            for s in sessions
            if (s.id, "heat") not in applied
        )
        changes += [change for change in heat if change is not None]
    db.apply_changes(conn, changes)
    return changes


def _history(conn: sqlite3.Connection, cfg: SchedulingConfig, today: date) -> History:
    """Recent weekly leg load from stored activities, for the continuous re-plan."""
    this_week = monday(today)
    weeks = [
        this_week - timedelta(weeks=n) for n in range(cfg.continuous.chronic_load_weeks + 1, 0, -1)
    ]
    loads = {week: 0.0 for week in weeks}
    run_minutes: list[float] = []
    for activity in db.activities(conn):
        day = local_day(activity.started_at)
        if monday(day) in loads:
            loads[monday(day)] += activity.load or 0.0
        if activity.sport == "run" and day >= today - timedelta(days=28):
            run_minutes.append(activity.duration_s / 60)
    blocks = [c for c in db.list_commitments(conn) if c.kind == "block" and c.active]
    blocked = {
        week
        for week in weeks
        for c in blocks
        if c.start_date and c.end_date
        and date.fromisoformat(c.start_date) <= week
        and date.fromisoformat(c.end_date) >= week + timedelta(days=6)
    }  # fmt: skip
    if not run_minutes:
        raise RuntimeError("continuous re-plan needs at least one run in the last 28 days")
    return History(
        weekly_loads=[WeekLoad(w, loads[w], blocked=w in blocked) for w in weeks],
        typical_easy_duration_min=round(median(run_minutes)),
        checkins=db.checkins(conn, today - timedelta(days=28)),
        busy=to_busy(db.calendar_events(conn)),
    )


def _replan(
    conn: sqlite3.Connection, cfg: ConfigNode, typed: SchedulingConfig, today: date, now: datetime
) -> str:
    plan = db.active_plan(conn)
    if plan is None or plan.id is None or plan.mode != "continuous":
        return "skipped: no active continuous plan"
    athlete = db.get_athlete(conn)
    if athlete is None:
        raise RuntimeError("continuous re-plan needs an athlete profile")
    result = replan_continuous_if_due(
        conn, plan.id, athlete, _history(conn, typed, today), today, now,
        db.list_commitments(conn), cfg,
    )  # fmt: skip
    return "ok: not due" if result is None else f"ok: {len(result.sessions)} sessions planned"


def run_sync(
    conn: sqlite3.Connection,
    cfg: ConfigNode,
    now_utc: datetime,
    *,
    client: httpx.Client | None = None,
) -> dict[str, str]:
    """Run every sync step in order and return each step's one-line outcome."""
    typed = load_scheduling_config(cfg)
    today = now_utc.astimezone(TZ).date()
    steps: dict[str, Callable[[], str]] = {
        "inbox": lambda: "ok: {} added, {} duplicates, {} failed".format(
            *vars(ingest_inbox(conn, typed)).values()
        ),
        "calendar": lambda: _calendar(conn, typed, today, client),
        "forecast": lambda: _forecast(conn, typed, client),
        "matching": lambda: match_activities(conn, typed),
        "adaptation": lambda: f"ok: {len(adapt_plan(conn, typed, today))} changes",
        "replan": lambda: _replan(conn, cfg, typed, today, now_utc),
    }
    report: dict[str, str] = {}
    for name, step in steps.items():
        try:
            report[name] = step()
        except Exception as exc:  # isolation: one failing step never stops the others
            if conn.in_transaction:
                conn.rollback()
            log.exception("sync step %s failed", name)
            report[name] = f"error: {exc}"
        else:
            db.set_kv(conn, f"sync.{name}.last_ok", now_utc.isoformat())
    db.set_kv(conn, "sync.last_run", now_utc.isoformat())
    db.set_kv(conn, "sync.last_report", json.dumps(report))
    log.info("sync: %s", "; ".join(f"{k} {v}" for k, v in report.items()))
    return report
