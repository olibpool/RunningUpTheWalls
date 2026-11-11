from __future__ import annotations

from datetime import date

import pytest

from runcoach.config import load_config
from runcoach.domain.models import Athlete
from runcoach.engine.time_trial import (
    apply_time_trial_result,
    time_trial_session,
    vdot_from_time_trial,
)
from runcoach.engine.vdot import vdot_from_race


def _athlete() -> Athlete:
    return Athlete(
        vdot=45.0,
        vdot_updated_at="2026-11-09T00:00:00+00:00",
        runs_per_week=3,
    )


def test_vdot_from_time_trial_validates_and_uses_race_calculation() -> None:
    assert vdot_from_time_trial(5000, 25 * 60) == vdot_from_race(5000, 25 * 60)
    for distance, duration in ((0, 1000), (5000, 0), (float("inf"), 1000), (5000, float("nan"))):
        with pytest.raises(ValueError):
            vdot_from_time_trial(distance, duration)


def test_time_trial_session_uses_configured_structure() -> None:
    session = time_trial_session(date(2026, 11, 30), load_config(env={}))

    assert session.kind == "time_trial"
    assert session.date == date(2026, 11, 30)
    assert session.duration_min == 50
    assert session.distance_m == 5000
    assert [step.kind for step in session.steps] == ["warmup", "work", "cooldown"]


def test_time_trial_result_requires_confirmation_and_utc_timestamp() -> None:
    athlete = _athlete()
    unchanged = apply_time_trial_result(
        athlete,
        5000,
        25 * 60,
        confirmed=False,
        confirmed_at="not-a-timestamp",
    )
    assert unchanged is athlete

    updated = apply_time_trial_result(
        athlete,
        5000,
        25 * 60,
        confirmed=True,
        confirmed_at="2026-11-30T12:00:00Z",
    )
    assert updated.vdot == vdot_from_race(5000, 25 * 60)
    assert updated.vdot_source == "time_trial"
    assert updated.vdot_updated_at == "2026-11-30T12:00:00+00:00"

    with pytest.raises(ValueError, match="UTC"):
        apply_time_trial_result(
            athlete,
            5000,
            25 * 60,
            confirmed=True,
            confirmed_at="2026-11-30T12:00:00+01:00",
        )
