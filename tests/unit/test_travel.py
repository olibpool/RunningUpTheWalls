from __future__ import annotations

from datetime import date

import pytest
from configs import typed

from runcoach.engine.templates import scale_session
from runcoach.engine.travel import minimal_travel_plan


def test_minimal_travel_plan_has_configured_runs_and_strides() -> None:
    config = typed()

    sessions = minimal_travel_plan(date(2026, 11, 9), 45, config)

    assert len(sessions) == config.minimal_plan.runs
    assert all(session.date == date(2026, 11, 9) for session in sessions)
    assert all(session.kind == "easy" for session in sessions)
    assert all(session.duration_min == 15 for session in sessions)
    steady, strides = sessions[0].steps
    assert steady.kind == "steady" and strides.kind == "repeat"
    assert strides.repeats == config.minimal_plan.strides_repeats
    # The easy running is a step, so trimming the run shortens it rather than leaving strides.
    assert 10 <= (scale_session(sessions[0], 0.8).duration_min or 0) < 15
    assert "strides" in sessions[0].reason
    assert all(session.start_time is None for session in sessions)


def test_minimal_travel_plan_rejects_bad_inputs() -> None:
    config = typed()

    with pytest.raises(ValueError, match="Monday"):
        minimal_travel_plan(date(2026, 11, 10), 45, config)
    with pytest.raises(ValueError, match="must be positive"):
        minimal_travel_plan(date(2026, 11, 9), 0, config)
