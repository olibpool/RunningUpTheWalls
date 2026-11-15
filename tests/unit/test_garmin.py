from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fitdata import activity_fit
from garminconnect import GarminConnectNotFoundError

from runcoach import db
from runcoach.config import ConfigNode, GarminConfig, load_config, load_scheduling_config
from runcoach.domain.models import Checkin, PlannedSession, Step
from runcoach.integrations.garmin import GarminClient, to_garmin_workout
from runcoach.integrations.watch import to_workout_spec
from runcoach.sync import run_sync

NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)  # a Monday
TODAY = NOW.date()


class FakeGarmin:
    def __init__(self) -> None:
        self.activities: list[dict[str, Any]] = []
        self.files: dict[str, bytes] = {}
        self.rhr: list[dict[str, Any]] = []
        self.sleep: dict[str, int] = {}  # calendar date -> sleepTimeSeconds
        self.sleep_calls: list[str] = []
        self.workouts: dict[str, dict[str, Any]] = {}
        self.scheduled: dict[str, str] = {}
        self.calls: list[str] = []
        self.fail_upload_after: int | None = None
        self._next = 100

    def get_activities_by_date(
        self, startdate: str, enddate: str | None = None,
        activitytype: str | None = None, sortorder: str | None = None,
    ) -> list[dict[str, Any]]:  # fmt: skip
        return self.activities

    def download_activity(self, activity_id: str, dl_fmt: Any = None) -> bytes:
        self.calls.append(f"download {activity_id}")
        return self.files[activity_id]

    def get_rhr_daily(self, start: str, end: str) -> list[dict[str, Any]]:
        return self.rhr

    def get_sleep_data(self, cdate: str) -> dict[str, Any]:
        self.sleep_calls.append(cdate)
        return {"dailySleepDTO": {"calendarDate": cdate, "sleepTimeSeconds": self.sleep.get(cdate)}}

    def upload_workout(self, workout_json: dict[str, Any] | list[Any] | str) -> dict[str, Any]:
        if self.fail_upload_after is not None and len(self.workouts) >= self.fail_upload_after:
            raise ConnectionError("network down")
        self._next += 1
        assert isinstance(workout_json, dict)
        self.workouts[str(self._next)] = workout_json
        self.calls.append("upload")
        return {"workoutId": self._next}

    def schedule_workout(self, workout_id: int | str, date_str: str) -> dict[str, Any]:
        self.scheduled[str(workout_id)] = date_str
        self.calls.append("schedule")
        return {"workoutScheduleId": 9000 + int(workout_id)}

    def delete_workout(self, workout_id: int | str) -> Any:
        self.calls.append(f"delete {workout_id}")
        if str(workout_id) not in self.workouts:
            raise GarminConnectNotFoundError("gone")
        del self.workouts[str(workout_id)]
        self.scheduled.pop(str(workout_id), None)


def _cfg(tmp_path: Path, *, pull: bool = True, push: bool = True) -> ConfigNode:
    cfg = load_config(local_path=tmp_path / "none.toml", env={})
    cfg.inbox.path = str(tmp_path / "inbox")
    cfg.garmin.enabled = pull
    cfg.export.backend = "garmin" if push else "none"
    return cfg


def _run(day: date, kind: str = "intervals") -> PlannedSession:
    steps = [
        Step(kind="warmup", duration_s=600),
        Step(kind="repeat", repeats=3, children=[
            Step(kind="work", duration_s=180, pace_min_s_per_km=240, pace_max_s_per_km=250),
            Step(kind="recover", duration_s=120),
        ]),
        Step(kind="cooldown", duration_s=600),
    ]  # fmt: skip
    return PlannedSession(
        date=day, start_time="07:00", sport="run", kind=kind, title="Intervals",
        duration_min=35, steps=steps, rpe=7.0, reason="Quality day.",
    )  # fmt: skip


def _sync(conn: Any, cfg: ConfigNode, fake: FakeGarmin, now: datetime = NOW) -> dict[str, str]:
    def connect(_: GarminConfig) -> GarminClient:
        return fake

    return run_sync(conn, cfg, now, garmin_connect=connect)


def test_garmin_mapping_numbers_steps_and_sets_pace_targets() -> None:
    from runcoach.domain.models import Session

    session = Session(id=1, plan_id=1, **_run(TODAY).model_dump())
    workout = to_garmin_workout(to_workout_spec(session, 2), "Quality day.", 32)
    assert workout["workoutName"] == "RC W2 Intervals"
    assert workout["estimatedDurationInSecs"] == 600 + 3 * 300 + 600
    warmup, repeat, cooldown = workout["workoutSegments"][0]["workoutSteps"]
    assert [warmup["stepOrder"], repeat["stepOrder"], cooldown["stepOrder"]] == [1, 2, 5]
    assert repeat["numberOfIterations"] == 3 and repeat["type"] == "RepeatGroupDTO"
    work, rest = repeat["workoutSteps"]
    assert (work["stepOrder"], rest["stepOrder"], work["childStepId"]) == (3, 4, 1)
    assert work["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    # m/s, slower pace first
    assert (work["targetValueOne"], work["targetValueTwo"]) == (1000 / 250, 1000 / 240)
    assert rest["targetType"]["workoutTargetTypeKey"] == "no.target"


def test_push_is_idempotent_replaces_changes_and_removes_dropped(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "g.db")
    cfg = _cfg(tmp_path, pull=False)
    plan_id = db.save_plan(conn, [_run(TODAY + timedelta(days=1)), _run(TODAY + timedelta(days=3)),
                                  _run(TODAY + timedelta(days=20))])  # fmt: skip
    fake = FakeGarmin()

    assert _sync(conn, cfg, fake)["watch"] == "ok: 2 pushed, 0 removed, 0 unchanged"
    assert sorted(fake.scheduled.values()) == ["2026-11-17", "2026-11-19"]
    fake.calls.clear()
    assert _sync(conn, cfg, fake)["watch"] == "ok: 0 pushed, 0 removed, 2 unchanged"
    assert fake.calls == []  # unchanged re-sync makes no calls

    first, second, _ = db.plan_sessions(conn, plan_id)
    db.apply_changes(conn, [
        _change(first.id, "modify", {"date": TODAY + timedelta(days=2)}),
        _change(second.id, "modify", {"status": "skipped"}),
    ])  # fmt: skip
    assert _sync(conn, cfg, fake)["watch"] == "ok: 1 pushed, 1 removed, 0 unchanged"
    assert list(fake.scheduled.values()) == ["2026-11-18"]
    assert set(db.garmin_pushes(conn)) == {first.id}

    # A workout deleted by hand in Garmin Connect doesn't break the next change.
    fake.workouts.clear()
    db.apply_changes(conn, [_change(first.id, "modify", {"date": TODAY + timedelta(days=4)})])
    assert _sync(conn, cfg, fake)["watch"].startswith("ok: 1 pushed")
    conn.close()


def _change(session_id: int, action: str, updates: dict[str, Any]) -> Any:
    from runcoach.adapt.rules import Change

    return Change(session_id, action, updates, "test", "test change")  # type: ignore[arg-type]


def test_failure_partway_keeps_finished_pushes(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "g.db")
    cfg = _cfg(tmp_path, pull=False)
    db.save_plan(conn, [_run(TODAY + timedelta(days=d)) for d in (1, 2, 3)])
    fake = FakeGarmin()
    fake.fail_upload_after = 2
    assert _sync(conn, cfg, fake)["watch"].startswith("error: network down")
    assert len(db.garmin_pushes(conn)) == 2
    fake.fail_upload_after = None
    fake.calls.clear()
    assert _sync(conn, cfg, fake)["watch"] == "ok: 1 pushed, 0 removed, 2 unchanged"
    assert fake.calls == ["upload", "schedule"]
    conn.close()


def test_pull_stores_new_activities_once_matches_them_and_fills_resting_hr(
    tmp_path: Path,
) -> None:
    conn = db.connect(tmp_path / "g.db")
    cfg = _cfg(tmp_path, push=False)
    db.save_plan(conn, [_run(TODAY)])
    start = datetime(2026, 11, 16, 7, tzinfo=timezone.utc)
    laps = [(600, 1800), *[(180, 735), (120, 300)] * 3, (600, 1800)]
    fake = FakeGarmin()
    fake.activities = [
        {"activityId": 555, "startTimeGMT": "2026-11-16 07:00:00"},
        {"activityId": 556, "startTimeGMT": "2026-11-15 09:00:00"},  # a manual entry, no file
    ]
    fake.files = {"555": activity_fit(start, laps)}
    db.save_checkin(conn, Checkin(date=TODAY - timedelta(days=1), resting_hr=50))
    fake.rhr = [
        {"calendarDate": (TODAY - timedelta(days=1)).isoformat(), "value": 58},
        {"calendarDate": TODAY.isoformat(), "value": 52},
    ]

    report = _sync(conn, cfg, fake)
    assert (
        report["garmin"] == "ok: 1 activities added, 1 skipped, 1 resting HR days, 0 sleep nights"
    )
    assert report["matching"].startswith("ok: 1 matched")
    (activity,) = db.activities(conn)
    assert activity.source == "garmin" and activity.external_id == "555"
    assert len(activity.laps) == 8 and activity.session_id is not None
    hr = {c.date: c.resting_hr for c in db.checkins(conn)}
    assert hr == {TODAY - timedelta(days=1): 50, TODAY: 52}  # your own entry wins

    fake.calls.clear()
    _sync(conn, cfg, fake)
    assert fake.calls == ["download 556"]  # 555 is never downloaded again
    conn.close()


def test_failed_login_is_tried_once_and_other_steps_still_run(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "g.db")
    attempts = []

    def connect(_: GarminConfig) -> GarminClient:
        attempts.append(1)
        raise RuntimeError("Garmin login failed (GarminConnectAuthenticationError)")

    report = run_sync(conn, _cfg(tmp_path), NOW, garmin_connect=connect)
    assert report["garmin"].startswith("error: Garmin login failed")
    assert report["watch"].startswith("error: Garmin login failed")
    assert report["matching"].startswith("skipped") and len(attempts) == 1
    conn.close()


def test_garmin_steps_are_skipped_when_off(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "g.db")
    report = _sync(conn, _cfg(tmp_path, pull=False, push=False), FakeGarmin())
    assert report["garmin"].startswith("skipped") and report["watch"].startswith("skipped")
    assert load_scheduling_config(_cfg(tmp_path)).garmin.token_dir.startswith("~")
    conn.close()


@pytest.mark.parametrize("name_max", [10])
def test_long_workout_names_are_cut_to_the_limit(name_max: int) -> None:
    from runcoach.domain.models import Session

    session = Session(id=1, plan_id=1, **_run(TODAY).model_dump())
    assert len(to_garmin_workout(to_workout_spec(session, 12), "", name_max)["workoutName"]) == 10


def test_sleep_fills_missing_nights_only_and_keeps_your_own(tmp_path) -> None:
    from datetime import date

    from runcoach import db
    from runcoach.domain.models import Checkin
    from runcoach.integrations.garmin import pull_sleep

    conn = db.connect(tmp_path / "s.db")
    cfg = load_scheduling_config(load_config(env={})).garmin
    today = date(2026, 11, 15)
    db.save_checkin(conn, Checkin(date=date(2026, 11, 14), sleep_hours=8.0))  # typed by you
    fake = FakeGarmin()
    fake.sleep = {"2026-11-15": 6 * 3600 + 900, "2026-11-14": 4 * 3600, "2026-11-10": 7 * 3600}

    assert pull_sleep(conn, fake, cfg, today) == "2 sleep nights"  # first sync: full backfill
    nights = {c.date: c.sleep_hours for c in db.checkins(conn)}
    assert nights[date(2026, 11, 15)] == 6.25 and nights[date(2026, 11, 10)] == 7.0
    assert nights[date(2026, 11, 14)] == 8.0  # never overwritten
    assert len(fake.sleep_calls) == cfg.health_days_back  # every night but the one you typed

    fake.sleep_calls.clear()
    assert pull_sleep(conn, fake, cfg, today) == "0 sleep nights"
    assert fake.sleep_calls == []  # later syncs only ask about missing recent nights
    conn.close()
