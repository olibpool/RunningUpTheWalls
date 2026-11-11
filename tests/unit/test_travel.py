from __future__ import annotations

from datetime import date

import pytest

from runcoach.config import load_config, load_scheduling_config
from runcoach.engine.travel import minimal_travel_plan


def test_minimal_travel_plan_has_configured_runs_and_strides() -> None:
    config = load_scheduling_config(load_config(env={}))

    sessions = minimal_travel_plan(date(2026, 11, 9), 45, config)

    assert len(sessions) == config.minimal_plan.runs
    assert all(session.date == date(2026, 11, 9) for session in sessions)
    assert all(session.kind == "easy" for session in sessions)
    assert all(session.duration_min == 15 for session in sessions)
    assert sessions[0].steps[0].kind == "repeat"
    assert sessions[0].steps[0].repeats == config.minimal_plan.strides_repeats
    assert "strides" in sessions[0].reason
    assert all(session.start_time is None for session in sessions)


def test_minimal_travel_plan_rejects_bad_inputs() -> None:
    config = load_scheduling_config(load_config(env={}))

    with pytest.raises(ValueError, match="Monday"):
        minimal_travel_plan(date(2026, 11, 10), 45, config)
    with pytest.raises(ValueError, match="must be positive"):
        minimal_travel_plan(date(2026, 11, 9), 0, config)
