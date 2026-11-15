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
    typed_cfg = cfg if isinstance(cfg, SchedulingConfig) else load_scheduling_config(cfg)
    window_days = typed_cfg.readiness.rhr_window_days
    min_samples = typed_cfg.readiness.rhr_min_samples
    delta_bpm = typed_cfg.penalty.rhr_delta_bpm
    if window_days <= 0 or min_samples <= 0 or delta_bpm < 0:
        raise ValueError("RHR window and sample count must be positive; delta cannot be negative")

    start = today - timedelta(days=window_days - 1)
    relevant = [
        checkin
        for checkin in checkins
        if start <= checkin.date <= today and checkin.resting_hr is not None
    ]
    if any(checkin.resting_hr is not None and checkin.resting_hr <= 0 for checkin in relevant):
        raise ValueError("resting heart-rate readings must be positive")
    readings = [checkin.resting_hr for checkin in relevant if checkin.resting_hr is not None]
    if len(readings) < min_samples:
        return RestingHeartRateHint(
            baseline_bpm=None,
            today_bpm=None,
            suggests_extension=False,
        )

    today_readings = [
        checkin.resting_hr
        for checkin in relevant
        if checkin.date == today and checkin.resting_hr is not None
    ]
    baseline = float(median(readings))
    today_bpm = float(median(today_readings)) if today_readings else None
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
    typed_cfg = cfg if isinstance(cfg, SchedulingConfig) else load_scheduling_config(cfg)
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
