from __future__ import annotations

from datetime import date, timedelta

import pytest
from configs import typed

from runcoach.engine.fitness import Run, fitness_trend, run_estimate
from runcoach.engine.vdot import vo2_at_velocity

CFG = typed().fitness
TODAY = date(2026, 11, 18)


def test_one_run_inverts_heart_rate_reserve_to_vo2max() -> None:
    run = Run(TODAY, 60, 10_000, avg_hr=145)  # 6:00/km, HRR (145-45)/(195-45) = 2/3
    expected = 3.5 + (vo2_at_velocity(10_000 / 60) - 3.5) / (2 / 3)
    assert run_estimate(run, 45, 195, CFG) == pytest.approx(expected)
    assert 40 < expected < 45  # a plausible score for that run
    assert run_estimate(Run(TODAY, 10, 1700, 145), 45, 195, CFG) is None  # too short
    assert (
        run_estimate(Run(TODAY, 60, 10_000, avg_hr=190), 45, 195, CFG) is None
    )  # a race, not easy


def test_trend_needs_enough_runs_in_the_window() -> None:
    runs = [Run(TODAY - timedelta(days=3 * n), 45, 7_500, 145) for n in range(CFG.min_runs)]
    resting = {TODAY - timedelta(days=30): 45}
    trend = fitness_trend(runs, resting, 195, TODAY, CFG)
    assert trend and trend[-1][0] == date(2026, 11, 16)  # this week's point, on its Monday
    assert fitness_trend(runs[:-1], resting, 195, TODAY, CFG) == []  # one run short
    assert fitness_trend(runs, {}, 195, TODAY, CFG) == []  # no resting HR, no estimate
