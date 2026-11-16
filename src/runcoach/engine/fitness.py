"""Fitness from training heart rate: %HRR approximates %VO2 reserve (Swain & Leutholtz), applied
to Daniels' oxygen cost of running. Each steady run gives a VDOT-like estimate; the trend is the
median over a rolling window. Heat and hills bias it low, so it informs and never sets paces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from runcoach.config import FitnessConfig
from runcoach.engine.vdot import vo2_at_velocity

RESTING_VO2 = 3.5  # ml/kg/min, one MET


@dataclass(frozen=True)
class Run:
    day: date
    minutes: float
    metres: float
    avg_hr: int


def run_estimate(run: Run, resting_hr: float, max_hr: int, cfg: FitnessConfig) -> float | None:
    """VO2max estimate from one run, or None when it is too short or outside the HRR range."""
    if run.minutes < cfg.min_minutes or run.metres <= 0 or max_hr <= resting_hr:
        return None
    hrr = (run.avg_hr - resting_hr) / (max_hr - resting_hr)
    low, high = cfg.hrr_range
    if not low <= hrr <= high:
        return None
    vo2 = vo2_at_velocity(run.metres / run.minutes)
    return RESTING_VO2 + (vo2 - RESTING_VO2) / hrr


def fitness_trend(
    runs: list[Run],
    resting_hr: dict[date, int],
    max_hr: int,
    today: date,
    cfg: FitnessConfig,
) -> list[tuple[date, float]]:
    """Weekly points (Monday, estimate) over `trend_weeks`: the median of the runs in the
    `window_days` before each week's end, once there are `min_runs` of them."""
    if not resting_hr:
        return []
    fallback = median(resting_hr.values())

    def rest_on(day: date) -> float:
        known = [d for d in resting_hr if d <= day]
        return resting_hr[max(known)] if known else fallback

    estimates = [
        (run.day, value)
        for run in runs
        if (value := run_estimate(run, rest_on(run.day), max_hr, cfg)) is not None
    ]
    this_monday = today - timedelta(days=today.weekday())
    points = []
    for n in range(cfg.trend_weeks - 1, -1, -1):
        week = this_monday - timedelta(weeks=n)
        end = min(week + timedelta(days=6), today)
        window = [v for d, v in estimates if end - timedelta(days=cfg.window_days) < d <= end]
        if len(window) >= cfg.min_runs:
            points.append((week, round(median(window), 1)))
    return points
