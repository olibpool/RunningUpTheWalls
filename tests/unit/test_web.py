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
