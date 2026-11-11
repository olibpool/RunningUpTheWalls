from __future__ import annotations

from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from runcoach.config import load_config
from runcoach.domain.models import Athlete, Commitment
from runcoach.engine.continuous import History, WeekLoad, generate_continuous_plan_result

_TODAY = date(2026, 11, 9)


@settings(max_examples=30, deadline=None)
@given(
    weekly_loads=st.lists(
        st.floats(min_value=100, max_value=500, allow_nan=False), min_size=4, max_size=4
    ),
    ceiling=st.floats(min_value=200, max_value=800, allow_nan=False),
    block_offset=st.integers(min_value=0, max_value=35),
    block_days=st.integers(min_value=1, max_value=10),
    penalty_offset=st.integers(min_value=0, max_value=35),
    severity=st.integers(min_value=1, max_value=3),
)
def test_random_events_preserve_weekly_caps_and_day_constraints(
    weekly_loads: list[float],
    ceiling: float,
    block_offset: int,
    block_days: int,
    penalty_offset: int,
    severity: int,
) -> None:
    config = load_config(env={})
    config.continuous.weeks_ahead = 6
    athlete = Athlete(
        vdot=45.0,
        vdot_updated_at="2026-10-01T00:00:00+00:00",
        runs_per_week=3,
        climbing_target_per_week=0,
        max_weekly_load=ceiling,
        timezone="UTC",
    )
    history = History(
        weekly_loads=[
            WeekLoad(_TODAY - timedelta(weeks=4 - index), value)
            for index, value in enumerate(weekly_loads)
        ],
        typical_easy_duration_min=45,
    )
    block_start = _TODAY + timedelta(days=block_offset)
    penalty_day = _TODAY + timedelta(days=penalty_offset)
    events = [
        Commitment(
            kind="block",
            title="Random block",
            start_date=block_start.isoformat(),
            end_date=(block_start + timedelta(days=block_days - 1)).isoformat(),
        ),
        Commitment(
            kind="penalty",
            title="Random penalty",
            start_date=penalty_day.isoformat(),
            end_date=penalty_day.isoformat(),
            severity=severity,
        ),
    ]

    result = generate_continuous_plan_result(athlete, history, _TODAY, events, config)

    assert all(target <= ceiling for target in result.targets)
    for index, allowance in enumerate(result.allowed_loads):
        week_start = _TODAY + timedelta(weeks=index)
        planned_load = sum(
            session.leg_load
            for session in result.sessions
            if week_start <= session.date < week_start + timedelta(days=7)
        )
        assert planned_load <= allowance + 1e-6
    assert not any(
        block_start <= session.date < block_start + timedelta(days=block_days)
        for session in result.sessions
    )
    following_days = config.penalty.days_after[str(severity)]
    for offset in range(following_days + 1):
        constrained_day = penalty_day + timedelta(days=offset)
        assert not any(
            session.date == constrained_day
            and (
                session.kind in {"long", "tempo", "intervals", "time_trial"}
                or (session.rpe is not None and session.rpe > config.penalty.day_of_max_rpe)
            )
            for session in result.sessions
        )
