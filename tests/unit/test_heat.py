from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
from configs import typed
from pytest_httpx import HTTPXMock

from runcoach.adapt.heat import adjust_for_heat, pace_penalty_pct
from runcoach.domain.models import Session, Step
from runcoach.integrations.weather import fetch_forecast

CFG = typed()
TODAY = date(2027, 7, 12)


def _run(kind: str = "easy", day: date = TODAY) -> Session:
    step = Step(kind="steady", duration_s=2400, pace_min_s_per_km=300, pace_max_s_per_km=330)
    return Session(
        id=1, plan_id=1, date=day, start_time="18:00", sport="run", kind=kind,
        title=kind, steps=[step], duration_min=40, rpe=6.0, leg_load=240,
    )  # fmt: skip


def _forecast(temp: float, day: date = TODAY) -> dict[str, float]:
    return {f"{day.isoformat()}T18:00": temp}


@pytest.mark.parametrize(
    ("temp", "expected"), [(10, 0.0), (15, 0.0), (22.5, 0.02), (30, 0.065), (40, 0.12)]
)
def test_penalty_interpolates_between_points(temp: float, expected: float) -> None:
    assert pace_penalty_pct(temp, CFG.heat_pace_penalty_pct) == pytest.approx(expected)


def test_warm_day_slows_paces() -> None:
    change = adjust_for_heat(_run(), _forecast(25), TODAY, CFG)
    assert change is not None and change.rule == "heat"
    steps = change.updates["steps"]
    assert isinstance(steps, list)
    assert steps[0].pace_min_s_per_km == pytest.approx(300 * 1.03)
    assert "3%" in change.reason


def test_thresholds_downgrade_quality_and_suggest_indoors() -> None:
    hot = adjust_for_heat(_run("tempo"), _forecast(33), TODAY, CFG)
    assert hot is not None and hot.updates["kind"] == "easy"
    assert "indoors" not in hot.reason
    hotter = adjust_for_heat(_run("tempo"), _forecast(36), TODAY, CFG)
    assert hotter is not None and "indoors" in hotter.reason
    easy = adjust_for_heat(_run("easy"), _forecast(33), TODAY, CFG)
    assert easy is not None and "kind" not in easy.updates


def test_cool_far_off_or_unknown_hours_are_left_alone() -> None:
    assert adjust_for_heat(_run(), _forecast(12), TODAY, CFG) is None
    later = TODAY + timedelta(days=CFG.heat_pace_penalty_pct.days_ahead + 1)
    assert adjust_for_heat(_run(day=later), _forecast(30, later), TODAY, CFG) is None
    assert adjust_for_heat(_run(), {}, TODAY, CFG) is None


def test_fetch_forecast_reads_apparent_temperature(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json={
            "hourly": {
                "time": ["2027-07-12T17:00", "2027-07-12T18:00"],
                "apparent_temperature": [24.5, None],
            }
        }
    )
    with httpx.Client() as client:
        forecast = fetch_forecast(CFG.weather, client)
    assert forecast == {"2027-07-12T17:00": 24.5}
    request = httpx_mock.get_request()
    assert request is not None and "apparent_temperature" in str(request.url)
