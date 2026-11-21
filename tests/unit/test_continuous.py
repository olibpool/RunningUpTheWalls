from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

from configs import defaults

from runcoach.config import ConfigNode, load_scheduling_config
from runcoach.domain.models import Athlete, Commitment, PlannedSession
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
    config = defaults()
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

    # A cut-back week dips, then training resumes from the level before it.
    targets = result.targets
    assert targets[0] == 325.5 and targets[1] == 330.0
    assert 0.75 * targets[2] < targets[3] < 0.85 * targets[2]
    assert targets[4] >= targets[2]
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
    # Loads well above the two-short-runs floor, so the floor doesn't lift the held target.
    loads = [
        WeekLoad(date(2026, 10, 5), 1000.0),
        WeekLoad(date(2026, 10, 12), 1000.0),
        WeekLoad(date(2026, 10, 19), 1000.0),
        WeekLoad(date(2026, 10, 26), 1000.0),
        WeekLoad(date(2026, 11, 2), 1600.0),
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
    assert any("1.60x your recent average" in warning for warning in result.warnings)


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


def test_without_a_ceiling_growth_stops_at_the_default_and_never_cuts_current_load() -> None:
    from runcoach.engine.continuous import WeekLoad, default_ceiling
    from runcoach.engine.zones import zones_from_vdot

    cfg = _config(weeks_ahead=8)
    typed = load_scheduling_config(cfg)
    athlete = _athlete()  # no max_weekly_load
    zones = zones_from_vdot(athlete.vdot, typed.zones)
    ceiling = default_ceiling(athlete, zones, [], date(2026, 11, 9), 0.0, typed)
    assert ceiling > 0

    near = History(
        weekly_loads=[WeekLoad(date(2026, 10, 12) + timedelta(weeks=n), ceiling) for n in range(4)],
        typical_easy_duration_min=45,
    )
    result = generate_continuous_plan_result(athlete, near, date(2026, 11, 9), [], cfg)
    assert max(result.targets) <= ceiling + 1e-6  # holds at the ceiling instead of growing
    assert default_ceiling(athlete, zones, [], date(2026, 11, 9), 5 * ceiling, typed) == 5 * ceiling


def test_week_builder_costs_quality_first_and_keeps_the_long_run_longest() -> None:
    from runcoach.engine.continuous import _sessions_for_week
    from runcoach.engine.zones import zones_from_vdot

    typed = load_scheduling_config(_config())
    zones = zones_from_vdot(48.5, typed.zones)
    week = date(2026, 11, 16)
    for budget, profile in [(500, "speed"), (700, "threshold"), (1000, "speed"), (450, "aerobic")]:
        sessions = _sessions_for_week(week, budget, 4, profile, False, zones, typed, 0.5)
        long_run = next(s for s in sessions if s.kind == "long")
        easy = [s for s in sessions if s.kind == "easy"]
        assert sum(s.leg_load for s in sessions) <= budget * 1.05  # quality's real cost counted
        assert all(e.duration_min <= long_run.duration_min for e in easy)
        assert all(e.duration_min >= typed.templates.min_easy_minutes for e in easy)
        quality = [s for s in sessions if s.kind in {"tempo", "intervals"}]
        run_minutes = sum(s.duration_min or 0 for s in sessions)
        warm_cool = typed.templates.warmup_min + typed.templates.cooldown_min
        assert all(
            q.duration_min - warm_cool <= run_minutes * typed.templates.max_quality_share
            for q in quality
        )  # the hard part stays within its share of running time
    # Tiny budgets give fewer runs rather than several too-short ones.
    small = _sessions_for_week(week, 200, 5, "aerobic", False, zones, typed)
    assert 1 <= len(small) < 5


def test_football_week_and_time_trial_week_shapes() -> None:
    football = Commitment(
        kind="fixed", sport="football", title="Football", weekday=1,
        start_time="19:30", duration_min=60, rpe=7.5,
    )  # fmt: skip
    cfg = _config(weeks_ahead=6)
    cfg.continuous.retest_every_weeks = 4
    result = generate_continuous_plan_result(
        _athlete(vdot_updated_at="2026-10-31T00:00:00+00:00"),
        History(
            weekly_loads=[
                WeekLoad(date(2026, 10, 12) + timedelta(weeks=n), 1100.0) for n in range(4)
            ],
            typical_easy_duration_min=50,
        ),
        date(2026, 11, 9),
        [football],
        cfg,
    )
    assert not result.violations
    trial_week = [
        s
        for s in result.sessions
        if date(2026, 11, 30) <= s.date < date(2026, 12, 7) and s.sport == "run"
    ]
    assert any(s.kind == "time_trial" for s in trial_week)
    assert sum(s.kind == "easy" for s in trial_week) >= 2  # the trial doesn't empty the week


def test_history_counts_climbing_as_the_planner_does(tmp_path) -> None:
    from runcoach import db
    from runcoach.domain.models import Activity
    from runcoach.sync import history_from_activities

    conn = db.connect(tmp_path / "h.db")
    for sport, load in [("run", 300.0), ("climb", 500.0)]:
        db.insert_activity(
            conn,
            Activity(
                source="manual", sport=sport, started_at="2026-11-03T08:00:00+00:00",
                duration_s=3600, distance_m=10000 if sport == "run" else None, load=load,
            ),
        )  # fmt: skip
    typed = load_scheduling_config(_config())
    history = history_from_activities(conn, typed, date(2026, 11, 12))
    conn.close()
    week = next(w for w in history.weekly_loads if w.week_start == date(2026, 11, 2))
    assert week.leg_load == 300.0 + 500.0 * typed.load_factors.climb_leg


def test_running_never_drops_below_recent_running_when_football_fills_the_target() -> None:
    football = Commitment(
        kind="fixed", sport="football", title="Football", weekday=1,
        start_time="19:30", duration_min=60, rpe=7.5,
    )  # fmt: skip
    athlete = _athlete().model_copy(update={"climbing_target_per_week": 2})
    loads = [WeekLoad(date(2026, 10, 19) + timedelta(weeks=n), 550.0) for n in range(4)]
    cfg = _config(weeks_ahead=4)

    def run_minutes(minutes: float) -> dict[date, float]:
        result = generate_continuous_plan_result(
            athlete,
            History(weekly_loads=loads, typical_easy_duration_min=30, weekly_run_minutes=minutes),
            date(2026, 11, 16),
            [football],
            cfg,
        )
        assert not result.violations
        weeks: dict[date, float] = {}
        for s in result.sessions:
            if s.sport == "run":
                week = s.date - timedelta(days=s.date.weekday())
                weeks[week] = weeks.get(week, 0) + (s.duration_min or 0)
        return weeks

    # Football and climbing as entered use the whole target, yet a couple of short runs remain.
    assert len(run_minutes(0)) == 4
    weeks = run_minutes(90)
    # Week 4 of the program is a cut-back; the rest keep (nearly) your 90 minutes of running.
    assert len(weeks) == 4
    assert all(minutes >= 90 * 0.85 for week, minutes in weeks.items() if week != date(2026, 12, 7))


def test_low_running_gives_a_real_long_run_and_counts_this_weeks_done_load() -> None:
    # Football and climbing fill most of the load; ~76 min of running a week, logged hard.
    football = Commitment(
        kind="fixed", sport="football", title="Football", weekday=1,
        start_time="19:30", duration_min=40, rpe=7.5,
    )  # fmt: skip
    athlete = _athlete().model_copy(update={"runs_per_week": 4, "climbing_target_per_week": 2})
    loads = [WeekLoad(date(2026, 10, 26) + timedelta(weeks=n), 590.0) for n in range(3)]
    history = History(
        weekly_loads=loads, typical_easy_duration_min=32, weekly_run_minutes=76,
        weekly_run_load=340, longest_recent_run_km=9.0,
    )  # fmt: skip
    cfg = _config(weeks_ahead=3)

    def runs(today: date, h: History) -> list[PlannedSession]:
        result = generate_continuous_plan_result(athlete, h, today, [football], cfg)
        assert not result.violations
        return [s for s in result.sessions if s.sport == "run"]

    planned = runs(date(2026, 11, 16), history)
    for week in {s.date - timedelta(days=s.date.weekday()) for s in planned}:
        week_runs = [s for s in planned if s.date - timedelta(days=s.date.weekday()) == week]
        long_run = next(s for s in week_runs if s.kind == "long")
        assert long_run.duration_min >= 32  # never shorter than your usual run
        # The logged running load (340) is kept, not just its minutes at easy effort (228).
        assert sum(s.leg_load for s in week_runs) >= 340 * 0.95
    # Re-planned on Wednesday after a run and football: week one plans less from then on.
    done = replace(history, week_so_far={"run": 100.0, "football": 330.0})
    wednesday = date(2026, 11, 18)
    first_week = [s for s in runs(wednesday, history) if s.date < date(2026, 11, 23)]
    first_week_done = [s for s in runs(wednesday, done) if s.date < date(2026, 11, 23)]
    assert sum(s.leg_load for s in first_week_done) < sum(s.leg_load for s in first_week) - 100


def test_history_ignores_weeks_before_the_first_record(tmp_path) -> None:
    from runcoach import db
    from runcoach.domain.models import Activity
    from runcoach.sync import history_from_activities

    conn = db.connect(tmp_path / "h.db")
    for day in ("2026-11-03", "2026-11-05", "2026-11-10"):
        db.insert_activity(
            conn,
            Activity(
                source="manual", sport="run", started_at=f"{day}T08:00:00+00:00",
                duration_s=1800, distance_m=5000, load=120.0,
            ),
        )  # fmt: skip
    history = history_from_activities(conn, load_scheduling_config(_config()), date(2026, 11, 18))
    conn.close()
    known = [w.week_start for w in history.weekly_loads if not w.blocked]
    assert known == [date(2026, 11, 2), date(2026, 11, 9)]  # not three weeks of "rest" before
    assert chronic_load(history.weekly_loads, 4) == 180.0
    assert history.weekly_run_minutes == 45.0  # (60 + 30) / 2 known weeks


def test_stated_capacity_holds_a_real_long_run_with_easy_runs_around_it() -> None:
    # Sparse records (~76 min a week) but you say you run 25 km a week and a 10 km long run.
    athlete = _athlete().model_copy(
        update={"runs_per_week": 4, "weekly_km": 25.0, "long_run_km": 10.0}
    )
    loads = [WeekLoad(date(2026, 10, 26) + timedelta(weeks=n), 400.0) for n in range(3)]
    history = History(
        weekly_loads=loads, typical_easy_duration_min=32, weekly_run_minutes=76,
        longest_recent_run_km=9.0,
    )  # fmt: skip
    result = generate_continuous_plan_result(
        athlete, history, date(2026, 11, 16), [], _config(weeks_ahead=4)
    )
    assert not result.violations
    weeks: dict[date, list[PlannedSession]] = {}
    for s in (s for s in result.sessions if s.sport == "run"):
        weeks.setdefault(s.date - timedelta(days=s.date.weekday()), []).append(s)
    for week, runs in weeks.items():
        long_run = next(s for s in runs if s.kind == "long")
        cutback = week == date(2026, 12, 7)
        assert (long_run.distance_m or 0) >= (7_500 if cutback else 9_500)
        assert long_run.distance_m <= 11_000  # held near what you said, not grown 10% a week
        total_m = sum(s.distance_m or 0 for s in runs)
        assert len(runs) >= 2 and total_m >= (15_000 if cutback else 20_000)


def test_a_replan_late_in_the_day_places_nothing_in_the_past() -> None:
    history = History(
        weekly_loads=[WeekLoad(date(2026, 10, 26) + timedelta(weeks=n), 400.0) for n in range(3)],
        typical_easy_duration_min=32,
    )
    now = datetime(2026, 11, 18, 19, 50, tzinfo=timezone.utc)
    result = generate_continuous_plan_result(
        _athlete(), history, now.date(), [], _config(weeks_ahead=1), not_before=now
    )
    assert result.sessions and not result.violations
    assert all(
        datetime.combine(s.date, time.fromisoformat(s.start_time or "00:00"), tzinfo=timezone.utc)
        >= now
        for s in result.sessions
    )
