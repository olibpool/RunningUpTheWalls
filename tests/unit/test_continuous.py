from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from runcoach.config import ConfigNode, load_config, load_scheduling_config
from runcoach.domain.models import Athlete, Commitment
from runcoach.engine.continuous import (
    History,
    WeekLoad,
    acr,
    chronic_load,
    evaluate_acr,
    generate_continuous_plan_result,
)


def _athlete(
    *,
    max_weekly_load: float | None = None,
    vdot_updated_at: str = "2026-11-09T00:00:00+00:00",
) -> Athlete:
    return Athlete(
        vdot=45.0,
        vdot_updated_at=vdot_updated_at,
        runs_per_week=3,
        climbing_target_per_week=0,
        max_weekly_load=max_weekly_load,
        timezone="UTC",
    )


def _history() -> History:
    return History(
        weekly_loads=[
            WeekLoad(date(2026, 10, 12), 280.0),
            WeekLoad(date(2026, 10, 19), 300.0),
            WeekLoad(date(2026, 10, 26), 320.0),
            WeekLoad(date(2026, 11, 2), 340.0),
        ],
        typical_easy_duration_min=45,
    )


def _config(*, weeks_ahead: int = 6) -> ConfigNode:
    config = load_config(env={})
    config.continuous.weeks_ahead = weeks_ahead
    return config


def test_chronic_load_ignores_blocked_weeks_and_uses_recent_n() -> None:
    loads = [
        WeekLoad(date(2026, 9, 28), 100.0),
        WeekLoad(date(2026, 10, 5), 500.0, blocked=True),
        WeekLoad(date(2026, 10, 12), 200.0),
        WeekLoad(date(2026, 10, 19), 300.0),
        WeekLoad(date(2026, 10, 26), 400.0),
    ]

    assert chronic_load(loads, n=2) == 350.0


def test_continuous_targets_follow_cutback_and_ceiling() -> None:
    result = generate_continuous_plan_result(
        _athlete(max_weekly_load=330.0),
        _history(),
        date(2026, 11, 9),
        [],
        _config(),
    )

    assert result.targets == [325.5, 330.0, 330.0, 264.0, 277.2, 291.06]
    assert all(target <= 330.0 for target in result.targets)
    assert result.allowed_loads[0] == 325.5
    assert not any(session.kind == "time_trial" for session in result.sessions)
    assert any(
        session.kind == "tempo" and date(2026, 11, 16) <= session.date < date(2026, 11, 23)
        for session in result.sessions
    )


def test_six_week_plan_obeys_penalty_holiday_and_return_ramp() -> None:
    events = [
        Commitment(
            kind="penalty",
            title="Drinks",
            start_date="2026-11-11",
            end_date="2026-11-11",
            severity=2,
        ),
        Commitment(
            kind="block",
            title="Holiday",
            start_date="2026-11-16",
            end_date="2026-11-25",
        ),
    ]

    result = generate_continuous_plan_result(
        _athlete(),
        _history(),
        date(2026, 11, 9),
        events,
        _config(),
    )

    assert not any(
        date(2026, 11, 16) <= session.date <= date(2026, 11, 25) for session in result.sessions
    )
    assert not any(
        session.kind in {"tempo", "intervals", "long", "climb_power"}
        and session.date in {date(2026, 11, 11), date(2026, 11, 12)}
        for session in result.sessions
    )
    assert result.allowed_loads[3] == result.targets[3] * 0.8
    assert result.allowed_loads[4] == result.targets[4]


def test_overlapping_contiguous_blocks_use_the_combined_ramp_duration() -> None:
    events = [
        Commitment(
            kind="block",
            title="Long break",
            start_date="2026-10-01",
            end_date="2026-11-09",
        ),
        Commitment(
            kind="block",
            title="Short extension",
            start_date="2026-11-10",
            end_date="2026-11-14",
        ),
    ]

    result = generate_continuous_plan_result(
        _athlete(),
        _history(),
        date(2026, 11, 15),
        events,
        _config(weeks_ahead=2),
    )

    assert result.allowed_loads[1] == result.targets[1] * 0.5


def test_replanned_window_keeps_original_cutback_cycle() -> None:
    history = _history()
    history = History(
        weekly_loads=history.weekly_loads,
        typical_easy_duration_min=history.typical_easy_duration_min,
        plan_start_date=date(2026, 11, 9),
    )

    result = generate_continuous_plan_result(
        _athlete(),
        history,
        date(2026, 11, 30),
        [],
        _config(weeks_ahead=1),
    )

    assert result.targets == [chronic_load(history.weekly_loads, n=4) * 0.8]


def test_nonminimal_block_overrides_overlapping_travel_plan_days() -> None:
    events = [
        Commitment(
            kind="block",
            title="Work trip",
            start_date="2026-11-16",
            end_date="2026-11-20",
            minimal_plan=True,
        ),
        Commitment(
            kind="block",
            title="No-running dates",
            start_date="2026-11-18",
            end_date="2026-11-21",
        ),
    ]

    result = generate_continuous_plan_result(
        _athlete(),
        _history(),
        date(2026, 11, 16),
        events,
        _config(weeks_ahead=1),
    )

    assert result.sessions
    assert all(
        session.kind == "easy" and "Minimal run during travel" in session.reason
        for session in result.sessions
    )
    assert not any(
        date(2026, 11, 18) <= session.date <= date(2026, 11, 21) for session in result.sessions
    )


def test_time_trial_is_added_to_retest_cutback_week() -> None:
    cfg = _config(weeks_ahead=6)
    cfg.continuous.retest_every_weeks = 4
    cfg.time_trial.estimated_5k_duration_min = 20

    result = generate_continuous_plan_result(
        _athlete(vdot_updated_at="2026-10-31T00:00:00+00:00"),
        _history(),
        date(2026, 11, 9),
        [],
        cfg,
    )

    time_trials = [session for session in result.sessions if session.kind == "time_trial"]
    assert len(time_trials) == 1
    assert date(2026, 11, 30) <= time_trials[0].date <= date(2026, 12, 6)
    assert time_trials[0].steps[0].kind == "warmup"
    assert time_trials[0].steps[1].kind == "work"
    assert time_trials[0].steps[1].distance_m == 5000
    assert time_trials[0].steps[2].kind == "cooldown"
    assert not any(
        "Combined leg-load cap remains exceeded" in warning for warning in result.warnings
    )
    trial_start = datetime.combine(
        time_trials[0].date,
        time.fromisoformat(time_trials[0].start_time or "00:00"),
        tzinfo=timezone.utc,
    )
    for session in result.sessions:
        if session.kind not in {"long", "tempo", "intervals", "time_trial"}:
            continue
        session_start = datetime.combine(
            session.date,
            time.fromisoformat(session.start_time or "00:00"),
            tzinfo=timezone.utc,
        )
        if session_start < trial_start:
            assert trial_start - session_start >= timedelta(hours=48)


def test_acr_excludes_acute_week_from_chronic_average_and_holds_target() -> None:
    loads = [
        WeekLoad(date(2026, 10, 5), 100.0),
        WeekLoad(date(2026, 10, 12), 100.0),
        WeekLoad(date(2026, 10, 19), 100.0),
        WeekLoad(date(2026, 10, 26), 100.0),
        WeekLoad(date(2026, 11, 2), 160.0),
    ]
    cfg = _config(weeks_ahead=2)
    typed_cfg = load_scheduling_config(cfg)

    assert acr(loads, typed_cfg.continuous.chronic_load_weeks) == 1.6
    status = evaluate_acr(loads, typed_cfg)
    assert status.warning is not None
    assert status.hold_next_week

    result = generate_continuous_plan_result(
        _athlete(),
        History(
            loads,
            typical_easy_duration_min=45,
            plan_start_date=date(2026, 10, 19),
        ),
        date(2026, 11, 9),
        [],
        cfg,
    )
    assert result.targets[0] == chronic_load(loads, typed_cfg.continuous.chronic_load_weeks)
    assert any("ACR 1.60" in warning for warning in result.warnings)


def test_acr_soft_warning_thresholds_do_not_raise_the_hold_threshold() -> None:
    cfg = load_scheduling_config(_config())
    weeks = [date(2026, 10, 5) + timedelta(weeks=index) for index in range(5)]

    low = evaluate_acr(
        [WeekLoad(day, 100.0 if index < 4 else 50.0) for index, day in enumerate(weeks)],
        cfg,
    )
    at_hold_boundary = evaluate_acr(
        [WeekLoad(day, 100.0 if index < 4 else 150.0) for index, day in enumerate(weeks)],
        cfg,
    )
    inside = evaluate_acr(
        [WeekLoad(day, 100.0 if index < 4 else 130.0) for index, day in enumerate(weeks)],
        cfg,
    )

    assert low.warning is not None and not low.hold_next_week
    assert at_hold_boundary.warning is not None and not at_hold_boundary.hold_next_week
    assert inside.warning is None and not inside.hold_next_week
