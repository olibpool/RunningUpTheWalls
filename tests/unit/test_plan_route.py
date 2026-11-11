from __future__ import annotations

from fastapi.testclient import TestClient

from runcoach.web.app import create_app


def test_generate_plan_route_returns_sessions() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/plans/generate",
        json={
            "distance_km": 10.0,
            "goal_date": "2027-03-14",
            "vdot": 49.8,
            "runs_per_week": 4,
            "current_weekly_km": 12.0,
            "race_name": "10k test",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "sessions" in payload
    assert isinstance(payload["sessions"], list)
    assert payload["sessions"]
    assert payload["sessions"][0]["title"]
