from __future__ import annotations

from datetime import date, timedelta

from runcoach.config import load_config
from runcoach.domain.models import Checkin
from runcoach.engine.readiness import resting_heart_rate_hint


def test_rhr_hint_uses_median_and_only_suggests_above_delta() -> None:
    today = date(2026, 11, 9)
    checkins = [
        Checkin(
            date=today - timedelta(days=offset),
            resting_hr=50 if offset else 55,
        )
        for offset in range(7)
    ]
    cfg = load_config(env={})

    hint = resting_heart_rate_hint(checkins, today, cfg)

    assert hint.baseline_bpm == 50.0
    assert hint.today_bpm == 55.0
    assert not hint.suggests_extension

    checkins[0] = Checkin(date=today, resting_hr=56)
    hint = resting_heart_rate_hint(checkins, today, cfg)
    assert hint.suggests_extension


def test_rhr_hint_requires_enough_recent_readings_and_today_value() -> None:
    today = date(2026, 11, 9)
    cfg = load_config(env={})
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
