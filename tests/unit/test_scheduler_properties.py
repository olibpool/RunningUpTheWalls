from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from runcoach.config import load_config, load_scheduling_config
from runcoach.domain.models import PlannedSession
from runcoach.engine.safety import check_plan
from runcoach.engine.scheduler import Busy, FixedInstance, schedule_week

_WEEK_START = date(2026, 11, 9)
_CONFIG = load_scheduling_config(load_config(env={}))


@settings(max_examples=30, deadline=None)
@given(
    fixed_day_offset=st.integers(min_value=0, max_value=6),
    busy_day_offset=st.integers(min_value=0, max_value=6),
    busy_start_hour=st.integers(min_value=6, max_value=20),
    busy_duration_min=st.integers(min_value=30, max_value=120),
)
def test_scheduled_sessions_respect_hard_constraints_with_random_events(
    fixed_day_offset: int,
    busy_day_offset: int,
    busy_start_hour: int,
    busy_duration_min: int,
) -> None:
    assume(fixed_day_offset != busy_day_offset)
    fixed = FixedInstance(
        date=_WEEK_START + timedelta(days=fixed_day_offset),
        title="Football",
        sport="football",
        start_time="19:00",
        duration_min=90,
        rpe=6.0,
    )
    busy_start = datetime.combine(
        _WEEK_START + timedelta(days=busy_day_offset),
        time(hour=busy_start_hour),
        tzinfo=timezone.utc,
    )
    busy = Busy(
        start=busy_start,
        end=busy_start + timedelta(minutes=busy_duration_min),
        title="Busy event",
    )
    sessions = [
        PlannedSession(
            date=_WEEK_START,
            sport="run",
            kind=kind,
            title=kind.title(),
            duration_min=duration,
            rpe=4.0,
        )
        for kind, duration in (("long", 120), ("tempo", 40), ("easy", 30), ("easy", 35))
    ]

    result = schedule_week(
        _WEEK_START,
        sessions,
        2,
        [fixed],
        [busy],
        [],
        _CONFIG,
        timezone_name="UTC",
    )
    planned = [placement.session for placement in result.placed if placement.session is not None]

    assert planned
    assert not check_plan(
        planned,
        _CONFIG,
        fixed=[fixed],
        busy=[busy],
        timezone_name="UTC",
        relaxed_weeks={_WEEK_START} if result.relaxed else set(),
    )
