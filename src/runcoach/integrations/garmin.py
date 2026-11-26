"""Garmin Connect through the unofficial garminconnect library: pull activities and resting
heart rate, push planned runs as structured workouts.

garminconnect (and its HTTP stack) is imported only inside the functions that talk to Garmin,
so the web app doesn't load it unless Garmin is used. Only the session token is stored, under
[garmin] token_dir; the password is never written anywhere.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from runcoach import db
from runcoach.config import GarminConfig, SchedulingConfig
from runcoach.display import monday
from runcoach.domain.models import Checkin, GarminPush, Session, Sport
from runcoach.ingest.fit import parse_fit_file
from runcoach.ingest.inbox import store_activity
from runcoach.integrations.watch import (
    RepeatGroup,
    SyncReport,
    WorkoutSpec,
    WorkoutStep,
    to_workout_spec,
)

log = logging.getLogger(__name__)


class GarminClient(Protocol):
    """The part of garminconnect.Garmin used here (a fake replaces it in tests)."""

    def get_activities_by_date(
        self,
        startdate: str,
        enddate: str | None = None,
        activitytype: str | None = None,
        sortorder: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def download_activity(self, activity_id: str, dl_fmt: Any = ...) -> bytes: ...

    def get_rhr_daily(self, start: str, end: str) -> list[dict[str, Any]]: ...

    def get_sleep_data(self, cdate: str) -> dict[str, Any]: ...

    def upload_workout(self, workout_json: dict[str, Any] | list[Any] | str) -> dict[str, Any]: ...

    def schedule_workout(self, workout_id: int | str, date_str: str) -> dict[str, Any]: ...

    def delete_workout(self, workout_id: int | str) -> Any: ...


def _token_dir(cfg: GarminConfig) -> str:
    return str(Path(cfg.token_dir).expanduser())


def login(cfg: GarminConfig, email: str, password: str, prompt_mfa: Callable[[], str]) -> None:
    """Log in once (interactively) and save the session token for later syncs."""
    from garminconnect import Garmin

    Garmin(email, password, prompt_mfa=prompt_mfa).login(_token_dir(cfg))


def logged_in(cfg: GarminConfig) -> bool:
    """True once a session token has been saved."""
    return Path(_token_dir(cfg)).expanduser().exists()


def start_login(cfg: GarminConfig, email: str, password: str) -> tuple[Any, Any] | None:
    """Log in from the web app: saves the token and returns None, or returns the pending
    login (client, state) when Garmin wants a one-time code. The pending login holds session
    cookies, so it is kept in memory only."""
    from garminconnect import Garmin

    api = Garmin(email, password, return_on_mfa=True)
    status, state = api.login()
    if status == "needs_mfa":
        return api, state
    api.client.dump(_token_dir(cfg))
    return None


def finish_login(cfg: GarminConfig, pending: tuple[Any, Any], code: str) -> None:
    """Finish a login that was waiting for its one-time code, and save the token."""
    api, state = pending
    api.resume_login(state, code)
    api.client.dump(_token_dir(cfg))


def login_problem(exc: Exception) -> str:
    """A safe message for a failed login; library messages may echo account details."""
    from garminconnect import (
        GarminConnectAuthenticationError,
        GarminConnectTooManyRequestsError,
    )

    if isinstance(exc, GarminConnectTooManyRequestsError):
        return "Garmin is limiting logins right now. Wait a few minutes and try again."
    if isinstance(exc, GarminConnectAuthenticationError):
        return "Garmin didn't accept that email, password or code."
    return f"Couldn't reach Garmin ({type(exc).__name__}). Try again in a moment."


def connect(cfg: GarminConfig) -> GarminClient:
    """A client from the saved session token; never prompts and never uses a password."""
    from garminconnect import Garmin

    api = Garmin()
    try:
        api.login(_token_dir(cfg))
    except Exception as exc:
        # Report only the type: library messages may echo account details.
        raise RuntimeError(
            f"Garmin login failed ({type(exc).__name__}); log in again in Settings"
        ) from None
    return api


def garmin_sport(item: dict[str, Any], keywords: dict[Sport, list[str]]) -> Sport | None:
    """Climb or football from Garmin's activity type or the activity's name, else None."""
    text = f"{(item.get('activityType') or {}).get('typeKey', '')} {item.get('activityName') or ''}"
    text = text.lower().replace("_", " ")
    return next((sport for sport, words in keywords.items() if any(w in text for w in words)), None)


def pull_activities(
    conn: sqlite3.Connection, client: GarminClient, cfg: SchedulingConfig, today: date
) -> str:
    """Store new Garmin activities from their original FIT files; climbing and football that
    Garmin files as "other" get their sport from the activity's type or name."""
    from garminconnect import Garmin

    start = today - timedelta(days=cfg.garmin.pull_days_back)
    added = failed = relabelled = 0
    for item in client.get_activities_by_date(start.isoformat(), today.isoformat()):
        activity_id = str(item.get("activityId", ""))
        sport = garmin_sport(item, cfg.garmin.sport_keywords)
        stored = db.find_activity(conn, "garmin", activity_id) if activity_id else None
        if stored is not None and sport:  # also fixes rows stored before the sport was known
            relabelled += db.relabel_activity(conn, stored, sport, cfg.default_rpe[sport])
        if not activity_id or stored is not None:
            continue
        gmt = item.get("startTimeGMT")  # "YYYY-MM-DD HH:MM:SS"; skips runs already uploaded
        if gmt and db.activity_starting_at(conn, f"{gmt.replace(' ', 'T')}+00:00") is not None:
            continue
        try:
            data = client.download_activity(activity_id, Garmin.ActivityDownloadFormat.ORIGINAL)
            activity = parse_fit_file(data, source="garmin")
        except Exception as exc:  # e.g. a manual entry has no file; the rest still come in
            log.warning("Garmin activity %s skipped: %s", activity_id, type(exc).__name__)
            failed += 1
            continue
        update: dict[str, Any] = {"external_id": activity_id}
        if activity.sport == "other" and sport:
            update["sport"] = sport
        result = store_activity(conn, activity.model_copy(update=update), cfg)
        added += result.added
    return (
        f"{added} activities added"
        + (f", {relabelled} relabelled" if relabelled else "")
        + (f", {failed} skipped" if failed else "")
    )


def pull_resting_hr(
    conn: sqlite3.Connection, client: GarminClient, cfg: GarminConfig, today: date
) -> str:
    """Copy Garmin's daily resting HR into check-ins, never over a value you entered."""
    if not cfg.health_days_back:
        return "resting HR off"
    start = today - timedelta(days=cfg.health_days_back)
    existing = {c.date: c for c in db.checkins(conn, start)}
    filled = 0
    for row in client.get_rhr_daily(start.isoformat(), today.isoformat()):
        day, value = date.fromisoformat(row["calendarDate"]), row.get("value")
        current = existing.get(day)
        if value is None or (current is not None and current.resting_hr is not None):
            continue
        base = current or Checkin(date=day)
        db.save_checkin(conn, base.model_copy(update={"resting_hr": int(value)}))
        filled += 1
    return f"{filled} resting HR days"


def pull_sleep(
    conn: sqlite3.Connection, client: GarminClient, cfg: GarminConfig, today: date
) -> str:
    """Copy each night's sleep into check-ins, never over a value you entered.

    Garmin's API is unofficial, so calls are kept few: each sync asks only for nights still
    missing among the last two, and the full `health_days_back` window once a day."""
    if not cfg.health_days_back:
        return "sleep off"
    backfill = db.get_kv(conn, "garmin.sleep_backfill") != today.isoformat()
    start = today - timedelta(days=cfg.health_days_back if backfill else 1)
    existing = {c.date: c for c in db.checkins(conn, start)}
    filled = 0
    day = start
    while day <= today:
        current = existing.get(day)
        if current is None or current.sleep_hours is None:
            summary = (client.get_sleep_data(day.isoformat()) or {}).get("dailySleepDTO") or {}
            seconds = summary.get("sleepTimeSeconds")
            if seconds:  # None or 0: not recorded (yet); asked again at a later sync
                base = current or Checkin(date=day)
                hours = round(seconds / 3600, 2)
                db.save_checkin(conn, base.model_copy(update={"sleep_hours": hours}))
                filled += 1
        day += timedelta(days=1)
    if backfill:
        db.set_kv(conn, "garmin.sleep_backfill", today.isoformat())
    return f"{filled} sleep nights"


# Garmin workout-service ids (garminconnect.workout.StepType / ConditionType / TargetType).
_STEP_TYPES = {"warmup": (1, "warmup"), "cooldown": (2, "cooldown"), "interval": (3, "interval"),
               "recovery": (4, "recovery"), "run": (3, "interval")}  # fmt: skip
_END = {"time": (2, "time"), "distance": (3, "distance"), "open": (1, "lap.button")}


def _garmin_step(step: WorkoutStep, order: int, child: int | None) -> dict[str, Any]:
    from garminconnect.workout import ExecutableStep

    type_id, type_key = _STEP_TYPES[step.kind]
    end_id, end_key = _END[step.end]
    target: dict[str, Any] = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
    values: dict[str, Any] = {}
    if step.pace_fast_s_per_km and step.pace_slow_s_per_km:
        # Pace ranges are speeds in m/s, and the slower pace is the lower value.
        target = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"}
        values = {
            "targetValueOne": 1000 / step.pace_slow_s_per_km,
            "targetValueTwo": 1000 / step.pace_fast_s_per_km,
        }
    return ExecutableStep(
        stepOrder=order,
        stepType={"stepTypeId": type_id, "stepTypeKey": type_key},
        endCondition={"conditionTypeId": end_id, "conditionTypeKey": end_key},
        endConditionValue=step.value,
        targetType=target,
        childStepId=child,
        **values,
    ).model_dump(exclude_none=True, mode="json")


def to_garmin_workout(spec: WorkoutSpec, description: str, name_max: int) -> dict[str, Any]:
    """The neutral workout as a Garmin running workout (P4-T3 library mapping)."""
    from garminconnect.workout import (
        RunningWorkout,
        WorkoutSegment,
        create_repeat_group,
    )

    steps: list[Any] = []
    order = 0
    for item in spec.steps:
        order += 1
        if isinstance(item, RepeatGroup):
            group_order = order
            children = []
            for child in item.steps:
                order += 1  # Garmin numbers steps across the whole workout
                children.append(_garmin_step(child, order, child=1))
            group = create_repeat_group(item.iterations, [], group_order)
            steps.append(group.model_dump(exclude_none=True, mode="json") | {
                "workoutSteps": children,
                "childStepId": 1,
            })  # fmt: skip
        else:
            steps.append(_garmin_step(item, order, child=None))
    duration = sum(
        (s.value or 0) * (item.iterations if isinstance(item, RepeatGroup) else 1)
        for item in spec.steps
        for s in (item.steps if isinstance(item, RepeatGroup) else [item])
        if s.end == "time"
    )
    workout = RunningWorkout(
        workoutName=spec.name[:name_max],
        estimatedDurationInSecs=round(duration),
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType={"sportTypeId": 1, "sportTypeKey": "running"},
                workoutSteps=[],
            )
        ],
        description=description[:500] or None,
    ).to_dict()
    workout["workoutSegments"][0]["workoutSteps"] = steps
    return workout


class GarminExporter:
    """Keeps Garmin Connect's calendar in step with the coming week's planned runs.

    Each push is saved as soon as it succeeds, so a failure partway through never repeats
    finished work; a session whose workout changed is replaced, and one that is no longer
    planned (skipped, moved off the window, re-planned away) is removed.
    """

    def __init__(
        self, conn: sqlite3.Connection, client: GarminClient, cfg: SchedulingConfig,
        plan_start: date, now_utc: datetime,
    ) -> None:  # fmt: skip
        self.conn, self.client, self.cfg = conn, client, cfg
        self.plan_start, self.now = plan_start, now_utc

    def _delete(self, workout_id: str) -> None:
        from garminconnect import GarminConnectNotFoundError

        try:
            self.client.delete_workout(workout_id)
        except GarminConnectNotFoundError:
            pass  # already deleted in Garmin Connect

    def sync_window(self, sessions: list[Session], start: date, end: date) -> SyncReport:
        wanted = {
            s.id: s
            for s in sessions
            if s.sport == "run" and s.status == "planned" and s.steps and start <= s.date < end
        }
        pushes = db.garmin_pushes(self.conn)
        removed = pushed = unchanged = 0
        for session_id, push in pushes.items():
            if session_id in wanted:
                continue
            if push.date >= start:
                self._delete(push.workout_id)
                removed += 1
            # Past workouts stay on the Garmin calendar as history; just stop tracking them.
            db.delete_garmin_push(self.conn, session_id)
        for session_id, s in sorted(wanted.items(), key=lambda item: item[1].date):
            week = (monday(s.date) - monday(self.plan_start)).days // 7 + 1
            workout = to_garmin_workout(
                to_workout_spec(s, week), s.reason, self.cfg.garmin.workout_name_max
            )
            digest = hashlib.sha256(
                json.dumps([workout, s.date.isoformat()], sort_keys=True).encode()
            ).hexdigest()
            old = pushes.get(session_id)
            if old is not None and old.hash == digest:
                unchanged += 1
                continue
            if old is not None:
                self._delete(old.workout_id)  # deleting a workout also clears its schedule
            workout_id = str(self.client.upload_workout(workout)["workoutId"])
            record = GarminPush(
                session_id=session_id, workout_id=workout_id, schedule_id=None,
                date=s.date, hash="", pushed_at=self.now.isoformat(),
            )  # fmt: skip
            db.save_garmin_push(self.conn, record)  # an empty hash re-pushes if scheduling fails
            scheduled = self.client.schedule_workout(workout_id, s.date.isoformat())
            schedule_id = (
                scheduled.get("workoutScheduleId") if isinstance(scheduled, dict) else None
            )
            db.save_garmin_push(
                self.conn,
                record.model_copy(
                    update={"schedule_id": str(schedule_id) if schedule_id else None,
                            "hash": digest}
                ),
            )  # fmt: skip
            pushed += 1
        return SyncReport(pushed=pushed, removed=removed, unchanged=unchanged)
