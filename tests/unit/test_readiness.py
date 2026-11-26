from __future__ import annotations

from datetime import date, timedelta

from configs import defaults

from runcoach.domain.models import Checkin
from runcoach.engine.readiness import resting_heart_rate_hint, sleep_hint


def test_rhr_hint_uses_median_and_only_suggests_above_delta() -> None:
    today = date(2026, 11, 9)
    checkins = [
        Checkin(
            date=today - timedelta(days=offset),
            resting_hr=50 if offset else 55,
        )
        for offset in range(7)
    ]
    cfg = defaults()

    hint = resting_heart_rate_hint(checkins, today, cfg)

    assert hint.baseline_bpm == 50.0
    assert hint.today_bpm == 55.0
    assert not hint.suggests_extension

    checkins[0] = Checkin(date=today, resting_hr=56)
    hint = resting_heart_rate_hint(checkins, today, cfg)
    assert hint.suggests_extension


def test_rhr_hint_requires_enough_recent_readings_and_today_value() -> None:
    today = date(2026, 11, 9)
    cfg = defaults()
    insufficient = [
        Checkin(date=today - timedelta(days=offset), resting_hr=50) for offset in range(6)
    ]
    assert resting_heart_rate_hint(insufficient, today, cfg).baseline_bpm is None

    no_today = [
        Checkin(date=today - timedelta(days=offset), resting_hr=50) for offset in range(1, 8)
    ]
    hint = resting_heart_rate_hint(no_today, today, cfg)
    assert hint.baseline_bpm == 50.0
    assert not hint.suggests_extension


def test_sleep_hint_flags_a_short_night_or_a_low_average_only() -> None:
    cfg = defaults()
    today = date(2026, 11, 15)

    def nights(*hours: float) -> list[Checkin]:
        return [Checkin(date=today - timedelta(days=i), sleep_hours=h) for i, h in enumerate(hours)]

    short = sleep_hint(nights(5.5, 8, 8), today, cfg)
    assert short.short and short.last_night_h == 5.5
    tired = sleep_hint(nights(6.2, 6.4, 6.3), today, cfg)
    assert tired.short and tired.average_h is not None and tired.average_h < 6.5
    fine = sleep_hint(nights(7.5, 6.0, 8), today, cfg)
    assert not fine.short
    assert not sleep_hint([], today, cfg).short
