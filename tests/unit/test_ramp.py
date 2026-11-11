from __future__ import annotations

from datetime import date

import pytest

from runcoach.config import load_config, load_scheduling_config
from runcoach.domain.models import PlannedSession
from runcoach.engine.ramp import ramp_for_block
from runcoach.engine.scheduler import compose_week


@pytest.mark.parametrize(
    ("days_off", "multipliers", "no_quality_weeks"),
    [
        (7, (0.8,), 0),
        (14, (0.6, 0.8, 1.0), 0),
        (40, (0.5, 0.65, 0.8, 0.9, 1.0), 2),
    ],
)
def test_ramp_for_block_selects_configured_profile(
    days_off: int,
    multipliers: tuple[float, ...],
    no_quality_weeks: int,
) -> None:
    config = load_scheduling_config(load_config(env={}))

    ramp = ramp_for_block(days_off, config)

    assert ramp.weekly_multipliers == multipliers
    assert ramp.no_quality_weeks == no_quality_weeks


def test_compose_week_multiplies_growth_ceiling_by_ramp_factor() -> None:
    config = load_scheduling_config(load_config(env={}))
    session = PlannedSession(
        date=date(2026, 11, 9),
        sport="run",
        kind="easy",
        title="Easy run",
        duration_min=100,
        rpe=4.0,
        leg_load=400.0,
    )

    result = compose_week(
        date(2026, 11, 9),
        [session],
        [],
        1000.0,
        0,
        False,
        config,
        ramp_factor=0.5,
    )

    assert result.allowed_leg_load == 550.0
    assert result.actual_leg_load == 400.0


def test_ramp_rejects_nonpositive_block_duration() -> None:
    config = load_scheduling_config(load_config(env={}))

    with pytest.raises(ValueError, match="days_off must be positive"):
        ramp_for_block(0, config)
