from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runcoach import db
from runcoach.config import load_config
from runcoach.engine.plan import PlanGenerationResult
from runcoach.engine.safety import Violation
from runcoach.web.app import create_app

FOOTBALL = {
    "form": "fixed",
    "title": "Football",
    "sport": "football",
    "weekday": "1",
    "start_time": "19:00",
    "duration_min": "90",
    "rpe": "7",
}
PLAN = {
    "distance": "10k",
    "goal_date": (date.today() + timedelta(weeks=10)).isoformat(),
    "race_distance": "5k",
    "race_time": "22:30",
    "current_weekly_km": "25",
    "runs_per_week": "3",
    "climbing_target_per_week": "0",
}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "web.db"


@pytest.fixture
def client(db_path: Path) -> TestClient:
    cfg = load_config(local_path=Path("does-not-exist.toml"), env={})
    return TestClient(create_app(db_path=db_path, cfg=cfg))


def _save_plan(client: TestClient) -> str:
    response = client.post("/plan/save", data=PLAN, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"]


def test_health_reports_the_migrated_schema(client: TestClient) -> None:
    payload = client.get("/healthz").json()
    assert payload["status"] == "ok"
    assert payload["db_version"] == 2


def test_home_redirects_to_an_empty_week_that_offers_a_plan(client: TestClient) -> None:
    response = client.get("/")
    assert response.url.path == "/week"
    assert "No active plan yet." in response.text
    assert 'href="/plan/new"' in response.text
    assert response.text.count('class="day') == 7


def test_events_add_validate_and_delete(client: TestClient, db_path: Path) -> None:
    assert client.post("/events", data=FOOTBALL, follow_redirects=False).status_code == 303
    missing_time = {**FOOTBALL, "start_time": ""}
    response = client.post("/events", data=missing_time)
    assert response.status_code == 400
    assert "needs a sport, start time and duration" in response.text
    drinks = {"form": "drinks", "date": date.today().isoformat(), "severity": "2"}
    assert client.post("/events", data=drinks, follow_redirects=False).status_code == 303
    holiday = {
        "form": "holiday",
        "start_date": "2027-08-01",
        "end_date": "2027-08-10",
        "holiday_type": "travel",
    }
    assert client.post("/events", data=holiday, follow_redirects=False).status_code == 303

    page = client.get("/events").text
    assert "Every Tuesday" in page and "travel with running" in page and "drinks" in page
    conn = db.connect(db_path)
    kinds = {c.kind: c for c in db.list_commitments(conn)}
    conn.close()
    assert kinds["block"].minimal_plan and kinds["penalty"].severity == 2
    client.post(f"/events/{kinds['fixed'].id}/delete")
    assert "Every Tuesday" not in client.get("/events").text


def test_plan_wizard_previews_saves_and_fills_the_week(client: TestClient) -> None:
    client.post("/events", data=FOOTBALL)
    assert client.get("/plan/new").status_code == 200

    preview = client.post("/plan/new", data=PLAN)
    assert preview.status_code == 200
    assert "Save this plan" in preview.text
    assert "VDOT" in preview.text and "Week of" in preview.text

    location = _save_plan(client)
    week = client.get(location).text
    assert "Long run" in week
    assert "Football" in week and ">fixed<" in week
    assert re.search(r"\d:\d\d-\d:\d\d/km", week)  # paces render
    assert "Week of" in client.get("/plan").text


def test_plan_wizard_rejects_bad_input_and_unsafe_plans(
    client: TestClient, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_fitness = {k: v for k, v in PLAN.items() if k not in {"race_distance", "race_time"}}
    response = client.post("/plan/new", data=no_fitness)
    assert response.status_code == 400
    assert "race distance and time, or a VDOT" in response.text

    too_soon = {**PLAN, "goal_date": (date.today() + timedelta(weeks=2)).isoformat()}
    assert "needs at least" in client.post("/plan/new", data=too_soon).text

    unsafe = PlanGenerationResult(
        sessions=[], dropped=[], warnings=[], violations=[Violation("weekly-rise", "too much")]
    )
    monkeypatch.setattr(
        "runcoach.web.routes.plans.generate_race_plan_result", lambda *a, **k: unsafe
    )
    response = client.post("/plan/save", data=PLAN)
    assert response.status_code == 400
    assert "fails the safety checks" in response.text
    conn = db.connect(db_path)
    assert db.active_plan(conn) is None
    conn.close()


def test_session_detail_done_and_skip(client: TestClient, db_path: Path) -> None:
    _save_plan(client)
    conn = db.connect(db_path)
    plan = db.active_plan(conn)
    assert plan is not None and plan.id is not None
    first, second = db.plan_sessions(conn, plan.id)[:2]
    conn.close()

    detail = client.get(f"/session/{first.id}").text
    assert "Steps" in detail and "Mark done" in detail

    card = client.post(
        f"/session/{first.id}/done", data={"rpe": "6"}, headers={"HX-Request": "true"}
    )
    assert card.status_code == 200
    assert f'id="session-{first.id}"' in card.text and "done" in card.text
    assert "<html" not in card.text  # a fragment for htmx to swap

    skipped = client.post(f"/session/{second.id}/skip", follow_redirects=False)
    assert skipped.status_code == 303

    conn = db.connect(db_path)
    assert db.get_session(conn, first.id).status == "done"  # type: ignore[union-attr]
    assert db.get_session(conn, second.id).status == "skipped"  # type: ignore[union-attr]
    activity = conn.execute("SELECT rpe, session_id FROM activity").fetchone()
    assert (activity["rpe"], activity["session_id"]) == (6.0, first.id)
    assert [a.rule for a in db.session_adjustments(conn, first.id)] == ["manual_done"]
    conn.close()
    assert "Marked done." in client.get(f"/session/{first.id}").text


def test_cross_site_posts_are_blocked(client: TestClient) -> None:
    response = client.post("/events", data=FOOTBALL, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    same_site = client.post(
        "/events",
        data=FOOTBALL,
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert same_site.status_code == 303


def test_logging_drinks_eases_the_next_hard_session_straight_away(
    client: TestClient, db_path: Path
) -> None:
    _save_plan(client)
    conn = db.connect(db_path)
    plan = db.active_plan(conn)
    assert plan is not None and plan.id is not None
    hard = next(s for s in db.plan_sessions(conn, plan.id) if s.kind in {"tempo", "intervals"})
    conn.close()

    night_before = (hard.date - timedelta(days=1)).isoformat()
    client.post("/events", data={"form": "drinks", "date": night_before, "severity": "2"})

    conn = db.connect(db_path)
    eased = db.get_session(conn, hard.id)
    conn.close()
    assert eased is not None and eased.kind == "easy" and "by feel" in eased.title


GPX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
<trkpt lat="51.5" lon="-0.12"><time>{start}</time></trkpt>
<trkpt lat="51.6" lon="-0.12"><time>{end}</time></trkpt>
</trkseg></trk></gpx>"""


def _gpx_for(session_date: date, start_time: str, minutes: int) -> bytes:
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo

    local = dt.combine(
        session_date, dt.strptime(start_time, "%H:%M").time(), ZoneInfo("Europe/London")
    )
    start = local.astimezone(ZoneInfo("UTC"))
    end = start + timedelta(minutes=minutes)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return GPX_TEMPLATE.format(start=start.strftime(fmt), end=end.strftime(fmt)).encode()


def _first_sessions(db_path: Path, count: int = 2):  # type: ignore[no-untyped-def]
    conn = db.connect(db_path)
    plan = db.active_plan(conn)
    assert plan is not None and plan.id is not None
    sessions = [s for s in db.plan_sessions(conn, plan.id) if s.sport == "run"][:count]
    conn.close()
    return sessions


def test_activity_upload_matches_and_manual_assign(client: TestClient, db_path: Path) -> None:
    _save_plan(client)
    first, second = _first_sessions(db_path)
    assert first.start_time and first.duration_min and second.start_time
    upload = client.post(
        "/activities/upload",
        files={
            "file": (
                "run.gpx",
                _gpx_for(first.date, first.start_time, first.duration_min),
                "application/gpx+xml",
            )
        },
        follow_redirects=False,
    )
    assert upload.status_code == 303
    conn = db.connect(db_path)
    assert db.get_session(conn, first.id).status == "done"  # type: ignore[union-attr]
    conn.close()

    # A run on the right day but far too short doesn't auto-match; assign it by hand.
    stray = _gpx_for(second.date, second.start_time, 5)
    client.post("/activities/upload", files={"file": ("short.gpx", stray, "application/gpx+xml")})
    page = client.get("/activities").text
    assert "Needs a session" in page and "Assign" in page
    conn = db.connect(db_path)
    (unmatched,) = db.activities(conn, unmatched_only=True)
    conn.close()
    client.post(f"/activities/{unmatched.id}/assign", data={"session_id": str(second.id)})
    conn = db.connect(db_path)
    assert db.get_session(conn, second.id).status == "done"  # type: ignore[union-attr]
    conn.close()

    bad = client.post("/activities/upload", files={"file": ("x.tcx", b"<x/>", "text/xml")})
    assert bad.status_code == 400 and "Unsupported file type" in bad.text


def test_checkin_niggle_changes_the_plan_and_reports_it(client: TestClient, db_path: Path) -> None:
    _save_plan(client)
    response = client.post(
        "/checkin", data={"feel": "3", "niggle": "left calf", "resting_hr": "52"}
    )
    assert response.status_code == 200
    assert "Saved." in response.text
    assert client.get("/checkin").text.count('value="left calf"') == 1
    conn = db.connect(db_path)
    (checkin,) = db.checkins(conn)
    conn.close()
    assert checkin.niggle == "left calf" and checkin.resting_hr == 52


def test_move_is_refused_when_unsafe_and_applied_when_safe(
    client: TestClient, db_path: Path
) -> None:
    _save_plan(client)
    conn = db.connect(db_path)
    plan = db.active_plan(conn)
    assert plan is not None and plan.id is not None
    sessions = db.plan_sessions(conn, plan.id)
    conn.close()
    long_run = next(s for s in sessions if s.kind == "long")
    hard = next(s for s in sessions if s.kind in {"tempo", "intervals"} and s.date < long_run.date)

    unsafe = client.post(
        f"/session/{hard.id}/move",
        data={"date": (long_run.date - timedelta(days=1)).isoformat(), "start_time": "07:00"},
    )
    assert unsafe.status_code == 400 and "Not moved" in unsafe.text

    easy = next(s for s in sessions if s.kind == "easy" and s.date < long_run.date)
    target = easy.date + timedelta(days=1)
    if any(s.date == target for s in sessions):
        target = easy.date  # same day, different time is always available
    safe = client.post(
        f"/session/{easy.id}/move",
        data={"date": target.isoformat(), "start_time": "12:00"},
        follow_redirects=False,
    )
    assert safe.status_code == 303
    conn = db.connect(db_path)
    moved = db.get_session(conn, easy.id)
    conn.close()
    assert moved is not None and (moved.date, moved.start_time) == (target, "12:00")


def test_settings_profile_time_trial_and_sync(client: TestClient, db_path: Path) -> None:
    profile = {"vdot": "45", "runs_per_week": "4", "climbing_target_per_week": "2"}
    assert client.post("/settings/athlete", data=profile, follow_redirects=False).status_code == 303
    client.post("/settings/time-trial", data={"distance": "5k", "time": "20:00"})
    conn = db.connect(db_path)
    athlete = db.get_athlete(conn)
    conn.close()
    assert athlete is not None and athlete.vdot_source == "time_trial"
    assert 49 < athlete.vdot < 51

    page = client.get("/settings").text
    assert "Google Calendar (iCal)" in page and "not set" in page
    started = client.post("/sync/run", headers={"HX-Request": "true"})
    assert "Sync running" in started.text
    status = client.get("/sync/status").text  # TestClient runs the background task first
    assert "inbox" in status and "Sync running" not in status


def test_static_files_are_cached_for_a_day(client: TestClient) -> None:
    response = client.get("/static/app.css")
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert "cache-control" not in client.get("/week").headers


def test_calendar_proposals_are_confirmed_not_assumed(client: TestClient, db_path: Path) -> None:
    from runcoach.domain.models import CalendarEvent

    conn = db.connect(db_path)
    db.cache_calendar_events(
        conn,
        [
            CalendarEvent(
                external_id="cal-drinks@2027-02-05T19:00:00+00:00",
                title="Drinks with the team",
                starts_at="2027-02-05T19:00:00+00:00",
                ends_at="2027-02-05T23:00:00+00:00",
            ),
            CalendarEvent(
                external_id="cal-dentist@2027-02-06T09:00:00+00:00",
                title="Dentist",
                starts_at="2027-02-06T09:00:00+00:00",
                ends_at="2027-02-06T09:30:00+00:00",
            ),
        ],
        window_start=date(2027, 2, 1),
        window_end=date(2027, 2, 28),
    )
    conn.close()

    page = client.get("/events").text
    assert "From your calendar" in page and "Drinks with the team" in page
    assert "Dentist" not in page
    conn = db.connect(db_path)
    assert db.list_commitments(conn) == []  # nothing active until confirmed
    conn.close()

    client.post(
        "/events/proposals/confirm", data={"external_id": "cal-drinks@2027-02-05T19:00:00+00:00"}
    )
    conn = db.connect(db_path)
    (drinks,) = db.list_commitments(conn)
    conn.close()
    assert drinks.kind == "penalty" and drinks.active and drinks.source == "gcal"
    assert "From your calendar" not in client.get("/events").text


def test_continuous_mode_needs_a_profile_and_history_then_saves(
    client: TestClient, db_path: Path
) -> None:
    assert "athlete profile" in client.get("/plan/continuous").text
    client.post(
        "/settings/athlete",
        data={"vdot": "45", "runs_per_week": "3", "climbing_target_per_week": "0"},
    )
    assert "Upload a few recent runs" in client.get("/plan/continuous").text

    four_weeks_ago = date.today() - timedelta(days=20)
    for offset in range(0, 18, 3):  # a run every third day for the last few weeks
        day = four_weeks_ago + timedelta(days=offset)
        gpx = _gpx_for(day, "07:00", 40 + offset)
        client.post(
            "/activities/upload", files={"file": (f"r{offset}.gpx", gpx, "application/gpx+xml")}
        )

    preview = client.get("/plan/continuous")
    assert "Start this plan" in preview.text, preview.text
    saved = client.post("/plan/continuous/save", follow_redirects=False)
    assert saved.status_code == 303
    conn = db.connect(db_path)
    plan = db.active_plan(conn)
    conn.close()
    assert plan is not None and plan.mode == "continuous"


def test_pwa_manifest_and_service_worker_are_served_with_their_types(client: TestClient) -> None:
    manifest = client.get("/manifest.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.json()["start_url"] == "/week"
    worker = client.get("/sw.js")
    assert worker.headers["content-type"].startswith("text/javascript")
    assert worker.headers["cache-control"] == "no-cache"
    assert client.get("/static/icon-512.png").status_code == 200
    assert 'rel="manifest"' in client.get("/week").text
