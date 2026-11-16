from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from configs import defaults, typed
from fastapi.testclient import TestClient

from runcoach import config, db
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
    # A Saturday about 10 weeks out, so the race never sits beside Tuesday's football.
    "goal_date": (
        date.today() + timedelta(weeks=10, days=(5 - date.today().weekday()) % 7)
    ).isoformat(),
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
    cfg = defaults()
    cfg.garmin.token_dir = str(db_path.parent / "garmin")  # never the developer's own login
    return TestClient(create_app(db_path=db_path, cfg=cfg))


def _save_plan(client: TestClient) -> str:
    response = client.post("/plan/save", data=PLAN, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"]


def test_app_shell_health_caching_pwa_and_guide(client: TestClient) -> None:
    payload = client.get("/healthz").json()
    assert payload["status"] == "ok" and payload["db_version"] == 5
    assert client.get("/static/app.css").headers["cache-control"] == "public, max-age=86400"
    week = client.get("/week")
    assert "cache-control" not in week.headers
    assert 'rel="manifest"' in week.text and 'href="/guide"' in week.text
    manifest = client.get("/manifest.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.json()["start_url"] == "/week"
    worker = client.get("/sw.js")
    assert worker.headers["content-type"].startswith("text/javascript")
    assert worker.headers["cache-control"] == "no-cache"
    assert client.get("/static/icon-512.png").status_code == 200
    assert "User guide" in client.get("/guide").text


def test_home_guides_a_new_user_then_summarises_the_plan(client: TestClient) -> None:
    week = client.get("/week").text
    assert "No active plan yet." in week and 'href="/plan/new"' in week
    assert week.count('class="day') == 7
    assert "Three steps" in client.get("/").text
    client.post("/events", data=FOOTBALL)
    client.post("/plan/save", data=PLAN)
    page = client.get("/").text
    assert "Three steps" not in page
    assert "days to race day" in page and "Coming up" in page
    assert 'id="week-stats"' in page and "How this week fits together" in page
    assert "How are you today?" in page and "Drinks tonight" in page


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
    assert "Every Tuesday" in page and "Travel, short runs" in page and "Night out" in page
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
    assert "Football" in week and ">every week<" in week
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
    assert 'id="week-stats" class="week-stats" hx-swap-oob="true"' in card.text
    assert json.loads(card.headers["HX-Trigger"])["toast"]

    # A boosted form wants the whole page, so it gets a plain redirect to follow.
    boosted = {"HX-Request": "true", "HX-Boosted": "true"}
    skipped = client.post(f"/session/{second.id}/skip", headers=boosted, follow_redirects=False)
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
    assert "Which session was it?" in page and "Assign" in page
    conn = db.connect(db_path)
    (unmatched,) = db.activities(conn, unmatched_only=True)
    conn.close()
    client.post(f"/activities/{unmatched.id}/assign", data={"session_id": str(second.id)})
    conn = db.connect(db_path)
    assert db.get_session(conn, second.id).status == "done"  # type: ignore[union-attr]
    conn.close()

    bad = client.post("/activities/upload", files={"file": ("x.tcx", b"<x/>", "text/xml")})
    assert bad.status_code == 400 and "Unsupported file type" in bad.text


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
    assert "Spotted in your calendar" in page and "Drinks with the team" in page
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
    assert "Spotted in your calendar" not in client.get("/events").text


def test_continuous_mode_needs_a_profile_and_history_then_saves(
    client: TestClient, db_path: Path
) -> None:
    assert "athlete profile" in client.get("/plan/continuous").text
    client.post(
        "/settings/athlete",
        data={"vdot": "45", "runs_per_week": "3", "climbing_target_per_week": "0"},
    )
    assert "Connect Garmin in Settings" in client.get("/plan/continuous").text

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


def test_garmin_watch_pieces_fit_download_scoring_and_prefill(
    client: TestClient, db_path: Path
) -> None:
    from datetime import datetime, timezone

    from fitdata import activity_fit

    from runcoach.domain.models import Activity

    _save_plan(client)
    conn = db.connect(db_path)
    plan = db.active_plan(conn)
    assert plan is not None and plan.id is not None
    quality = next(s for s in db.plan_sessions(conn, plan.id) if s.kind in {"tempo", "intervals"})

    fit = client.get(f"/session/{quality.id}/workout.fit")
    assert fit.status_code == 200 and fit.content[8:12] == b".FIT"
    assert "attachment" in fit.headers["content-disposition"]
    assert "Download as a watch workout" in client.get(f"/session/{quality.id}").text

    # A run whose laps line up with the steps gets a per-step verdict.
    from runcoach.adapt.compliance import executed_steps

    steps = executed_steps(quality.steps)
    laps = [
        (s.duration_s or 600, (s.duration_s or 600) * 1000 / (s.pace_max_s_per_km or 360))
        for s in steps
    ]
    start = datetime.combine(quality.date, datetime.min.time(), tzinfo=timezone.utc)
    from runcoach.ingest.fit import parse_fit_file

    parsed = parse_fit_file(activity_fit(start, laps))
    activity_id = db.insert_activity(conn, Activity(**{**parsed.model_dump(), "id": None}))
    db.link_activity(conn, activity_id, quality.id, rule="matched", reason="test")
    detail = client.get(f"/session/{quality.id}").text
    assert "How it went" in detail and "on pace" in detail and "Workout hit." in detail

    # Recorded runs fill in the weekly distance on a new plan.
    assert 'name="current_weekly_km" value=""' not in client.get("/plan/new").text
    conn.close()


def test_app_settings_save_and_reload(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_PATH", saved)
    form = {
        "weekday": "6:30-08:00, 18:00-20:00",
        "weekend": "",
        "buffer_minutes": "15",
        "weather_enabled": "on",
        "latitude": "51.5",
        "longitude": "-0.12",
        "ics_url": "https://example.com/secret.ics",
        "keywords_fixed": "Football, Netball",
        "export_backend": "fit",
    }
    assert client.post("/settings/app", data=form, follow_redirects=False).status_code == 303
    page = client.get("/settings").text
    assert "06:30-08:00, 18:00-20:00" in page and "football, netball" in page
    assert "secret.ics" not in page  # the secret is never echoed back

    # A blank address keeps the saved one; the CLI and sync see the saves through load_config.
    assert client.post("/settings/app", data={**form, "ics_url": ""}).status_code == 200
    reloaded = typed()
    assert reloaded.gcal.ics_url == "https://example.com/secret.ics"
    assert reloaded.availability.weekday == [("06:30", "08:00"), ("18:00", "20:00")]
    assert reloaded.availability.weekend == [] and reloaded.weather.enabled
    assert reloaded.export.backend == "fit" and not reloaded.garmin.enabled

    bad = client.post("/settings/app", data={**form, "weekday": "09:00-08:00"})
    assert bad.status_code == 400 and "ends before it starts" in bad.text
    assert config.read_settings(saved)["availability"]["weekday"] == [
        ["06:30", "08:00"],
        ["18:00", "20:00"],
    ]

    client.post("/settings/app", data={**form, "clear_ics_url": "on"})
    assert config.read_settings(saved)["gcal"]["ics_url"] == ""


def test_rating_an_activity_sets_its_load_and_leaves_the_list(
    client: TestClient, db_path: Path
) -> None:
    from datetime import datetime, timezone

    from runcoach.domain.models import Activity

    started = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn = db.connect(db_path)
    rated_id = db.insert_activity(
        conn, Activity(source="garmin", sport="run", started_at=started, duration_s=3000, load=200)
    )
    db.insert_activity(  # Done already records effort, so manual runs are never asked about
        conn, Activity(source="manual", sport="run", started_at=started, duration_s=1800)
    )
    conn.close()
    assert client.get("/").text.count('class="rate-row') == 1

    assert client.post(f"/activities/{rated_id}/rpe", data={"rpe": "11"}).status_code == 400
    assert client.post(f"/activities/{rated_id}/rpe", data={"rpe": "0"}).status_code == 400
    assert client.post("/activities/9999/rpe", data={"rpe": "5"}).status_code == 404
    done = client.post(
        f"/activities/{rated_id}/rpe", data={"rpe": "7"}, headers={"HX-Request": "true"}
    )
    assert done.status_code == 200 and done.text == ""
    assert "toast" in done.headers["HX-Trigger"]

    conn = db.connect(db_path)
    row = conn.execute("SELECT rpe, load FROM activity WHERE id = ?", (rated_id,)).fetchone()
    conn.close()
    assert (row["rpe"], row["load"]) == (7.0, 350.0)  # 7 x 50 min
    assert 'class="rate-row' not in client.get("/").text


def test_session_page_shows_fuel_and_pacing_for_the_race(client: TestClient, db_path: Path) -> None:
    _save_plan(client)
    conn = db.connect(db_path)
    plan = db.active_plan(conn)
    assert plan is not None and plan.id is not None
    race = next(s for s in db.plan_sessions(conn, plan.id) if s.kind == "race")
    conn.close()
    page = client.get(f"/session/{race.id}").text
    assert "Fuel and pacing" in page and "Pacing:" in page


def test_sync_chip_starts_a_due_sync_and_asks_the_page_to_refresh(
    client: TestClient, db_path: Path
) -> None:
    chip = client.get("/sync/chip")  # never synced, so opening the app starts one
    assert "Syncing" in chip.text and "HX-Trigger" not in chip.headers
    done = client.get("/sync/chip?was=running")  # TestClient ran the sync after the response
    assert "Synced now" in done.text and "synced" in done.headers["HX-Trigger"]
    conn = db.connect(db_path)
    assert db.get_kv(conn, "sync.last_run") is not None
    conn.close()
    assert "HX-Trigger" not in client.get("/sync/chip").headers  # fresh: no new sync

    client.app.state.cfg.sync.auto_minutes = 0  # type: ignore[attr-defined]
    conn = db.connect(db_path)
    db.delete_kv(conn, "sync.last_run")
    conn.close()
    assert "Sync now" in client.get("/sync/chip").text  # off: nothing starts by itself


def test_garmin_connects_from_settings_with_a_one_time_code(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from runcoach.integrations import garmin

    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    token_dir = Path(client.app.state.cfg.garmin.token_dir)  # type: ignore[attr-defined]
    seen: list[str] = []

    def start(cfg: Any, email: str, password: str) -> tuple[str, str]:
        if password != "right":
            raise garmin_errors.GarminConnectAuthenticationError("401 for someone@example.com")
        return "client", "state"

    def finish(cfg: Any, pending: Any, code: str) -> None:
        seen.append(code)
        token_dir.mkdir()

    import garminconnect as garmin_errors

    monkeypatch.setattr(garmin, "start_login", start)
    monkeypatch.setattr(garmin, "finish_login", finish)
    assert "Connect Garmin" in client.get("/settings").text

    wrong = client.post("/garmin/login", data={"email": "a@b.co", "password": "nope"})
    assert wrong.status_code == 400 and "accept that email" in wrong.text
    assert "someone@example.com" not in wrong.text  # library messages never reach the page

    client.post("/garmin/login", data={"email": "a@b.co", "password": "right"})
    assert "one-time code" in client.get("/settings").text
    assert client.post("/garmin/code", data={"code": "abc"}).status_code == 400
    client.post("/garmin/code", data={"code": " 123456 "})
    assert seen == ["123456"]
    page = client.get("/settings").text
    assert "Connected" in page and "one-time code" not in page
    assert json.loads((tmp_path / "settings.json").read_text())["garmin"]["enabled"] is True


def test_today_takes_a_feeling_and_a_night_out_in_one_tap(
    client: TestClient, db_path: Path
) -> None:
    _save_plan(client)
    htmx = {"HX-Request": "true"}
    rough = client.post("/checkin/feel", data={"feel": "2"}, headers=htmx)
    assert "Make today easy" in rough.text  # a low score offers an easier day
    assert client.post("/checkin/feel", data={"feel": "9"}, headers=htmx).status_code == 400
    conn = db.connect(db_path)
    assert [c.feel for c in db.checkins(conn)] == [2]
    conn.close()

    # A night out logged the evening before a hard session eases it straight away.
    conn = db.connect(db_path)
    hard = next(s for s in db.active_sessions(conn) if s.kind in {"tempo", "intervals"})
    conn.close()
    night_before = (hard.date - timedelta(days=1)).isoformat()
    night = {"form": "drinks", "reply": "1", "date": night_before, "severity": "2"}
    reply = client.post("/events", data=night, headers=htmx)
    assert reply.status_code == 200 and "Got it." in reply.text and "<html" not in reply.text
    conn = db.connect(db_path)
    eased = db.get_session(conn, hard.id)
    conn.close()
    assert eased is not None and eased.kind == "easy" and "by feel" in eased.title
    assert "Night out" in client.get("/events").text  # it is listed under Coming up


def test_progress_and_whole_plan_pages_show_every_sport(client: TestClient) -> None:
    _save_plan(client)
    progress = client.get("/progress").text
    assert "Streak" in progress and "Every day, last" in progress and "Personal bests" in progress
    assert "Add your maximum heart rate" in progress  # the fitness trend needs it
    plan = client.get("/plan").text
    assert "Load, week by week" in plan and 'class="season"' in plan and "Football" in plan


def test_life_offers_the_length_the_watch_records(client: TestClient, db_path: Path) -> None:
    from datetime import datetime, timezone

    from runcoach.domain.models import Activity

    client.post("/events", data=FOOTBALL)  # entered as 90 minutes
    conn = db.connect(db_path)
    for days in (2, 9):
        start = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        db.insert_activity(conn, Activity(source="garmin", sport="football", started_at=start,
                                          duration_s=40 * 60, load=280))  # fmt: skip
    (football,) = db.list_commitments(conn)
    conn.close()
    assert "Use 40 min" in client.get("/events").text
    client.post(f"/events/{football.id}/duration", data={"minutes": "40"})
    page = client.get("/events").text
    assert "Use 40 min" not in page and "40 min" in page
    assert client.post("/events/999/duration", data={"minutes": "40"}).status_code == 400


def test_check_in_round_trips_and_the_plan_reacts(client: TestClient, db_path: Path) -> None:
    _save_plan(client)
    assert "Fever or chest symptoms" in client.get("/checkin").text
    saved = client.post("/checkin", data={"feel": "3", "niggle": "left calf", "resting_hr": "52"})
    assert saved.status_code == 200 and "Saved." in saved.text
    assert client.get("/checkin").text.count('value="left calf"') == 1
    conn = db.connect(db_path)
    (checkin,) = db.checkins(conn)
    conn.close()
    assert checkin.niggle == "left calf" and checkin.resting_hr == 52

    assert client.post("/checkin", data={"sleep_hours": "5.3"}).status_code == 400  # quarters
    sick = {"sleep_hours": "5.25", "ill": "true", "fever": "true"}
    assert client.post("/checkin", data=sick).status_code == 200
    conn = db.connect(db_path)
    (checkin,) = db.checkins(conn)
    rules = {row["rule"] for row in conn.execute("SELECT rule FROM adjustment").fetchall()}
    conn.close()
    assert checkin.fever and checkin.ill and checkin.sleep_hours == 5.25
    assert {"ill_or_niggle", "illness_rest"} & rules
    for page in (client.get("/").text, client.get("/checkin").text):
        assert "You slept 5.2 h last night" in page and "Make today easy" in page
