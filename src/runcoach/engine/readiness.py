from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from runcoach.config import ConfigNode, SchedulingConfig, load_scheduling_config
from runcoach.domain.models import Checkin


@dataclass(frozen=True)
class RestingHeartRateHint:
    baseline_bpm: float | None
    today_bpm: float | None
    suggests_extension: bool


def resting_heart_rate_hint(
    checkins: list[Checkin],
    today: date,
    cfg: ConfigNode | SchedulingConfig,
) -> RestingHeartRateHint:
    typed_cfg = load_scheduling_config(cfg)
    start = today - timedelta(days=typed_cfg.readiness.rhr_window_days - 1)
    readings = {c.date: c.resting_hr for c in checkins if start <= c.date <= today and c.resting_hr}
    if len(readings) < typed_cfg.readiness.rhr_min_samples:
        return RestingHeartRateHint(baseline_bpm=None, today_bpm=None, suggests_extension=False)
    baseline = float(median(readings.values()))
    today_bpm = float(readings[today]) if today in readings else None
    delta_bpm = typed_cfg.penalty.rhr_delta_bpm
    return RestingHeartRateHint(
        baseline_bpm=baseline,
        today_bpm=today_bpm,
        suggests_extension=today_bpm is not None and today_bpm > baseline + delta_bpm,
    )


@dataclass(frozen=True)
class SleepHint:
    last_night_h: float | None
    average_h: float | None  # over the last `sleep_avg_nights` nights with a reading
    short: bool


def sleep_hint(
    checkins: list[Checkin], today: date, cfg: ConfigNode | SchedulingConfig
) -> SleepHint:
    """Suggest an easier day after a short night or several short-ish ones. Advisory only."""
    typed_cfg = load_scheduling_config(cfg)
    r = typed_cfg.readiness
    since = today - timedelta(days=r.sleep_avg_nights - 1)
    nights = {c.date: c.sleep_hours for c in checkins if c.sleep_hours is not None}
    if any(hours < 0 for hours in nights.values()):
        raise ValueError("sleep hours must not be negative")
    last = nights.get(today)
    recent = [hours for day, hours in nights.items() if since <= day <= today]
    average = sum(recent) / len(recent) if len(recent) == r.sleep_avg_nights else None
    short = (last is not None and last < r.short_sleep_h) or (
        average is not None and average < r.low_sleep_avg_h
    )
    return SleepHint(last_night_h=last, average_h=average, short=short)
