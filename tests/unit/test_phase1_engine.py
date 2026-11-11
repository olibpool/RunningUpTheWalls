from __future__ import annotations

from datetime import date

import pytest

from runcoach.config import load_config
from runcoach.domain.models import Commitment, PlanInput, PlannedSession
from runcoach.engine.load import duration_from_distance_and_pace, session_rpe_load
from runcoach.engine.plan import (
    InfeasibleGoal,
    generate_race_plan,
    generate_race_plan_result,
)
from runcoach.engine.progression import split_blocks, weekly_targets
from runcoach.engine.safety import check_plan
from runcoach.engine.vdot import vdot_from_race, velocity_at_vo2, vo2_at_velocity
from runcoach.engine.zones import zones_from_vdot


def test_vdot_values_are_close_to_expected() -> None:
    assert 49.0 < vdot_from_race(5000, 20 * 60) < 50.5
    assert 37.5 < vdot_from_race(5000, 25 * 60) < 39.5
    assert 50.0 < vdot_from_race(10000, 40 * 60) < 53.5


def test_vdot_inverts_vo2() -> None:
    assert abs(velocity_at_vo2(vo2_at_velocity(200.0)) - 200.0) < 1e-6


def test_zones_are_in_expected_order() -> None:
    zones = zones_from_vdot(50.0, None)
    assert zones.easy.min_s_per_km > zones.marathon.min_s_per_km > zones.threshold.min_s_per_km
    assert (
        zones.threshold.min_s_per_km > zones.interval.min_s_per_km > zones.repetition.min_s_per_km
    )
    assert zones.easy.min_s_per_km < zones.easy.max_s_per_km

    interval_pace = zones.interval.min_s_per_km
    threshold_pace = zones.threshold.min_s_per_km
    assert 230.0 < interval_pace < 245.0
    assert 255.0 < threshold_pace < 265.0


def test_weekly_targets_keep_growth_and_cutbacks_balanced() -> None:
    targets = weekly_targets(20.0, 8, "half", None)
    assert len(targets) == 8
    assert targets[0] > 20.0
    assert targets[1] > targets[0]
    assert targets[3] < targets[2]
    assert max(targets) <= 20.0 * (1.10**7) + 1e-9


def test_load_and_progression() -> None:
    assert session_rpe_load(8.0, 30) == 240.0
    assert duration_from_distance_and_pace(1000.0, 300.0) == pytest.approx(5.0, abs=1e-6)

    targets = weekly_targets(20.0, 6, "half", None)
    assert len(targets) == 6
    assert targets[0] > 20.0
    assert targets[3] < targets[2]
    blocks = split_blocks(8, "half", None)
    assert sum(block.weeks for block in blocks) == 8


def test_safety_rejects_bad_plan() -> None:
    sessions = [
        PlannedSession(
            date=date(2026, 1, 1),
            sport="run",
            kind="tempo",
            title="Tempo",
            steps=[],
            duration_min=30,
            distance_m=5000,
            rpe=6.0,
            leg_load=120.0,
            finger_load=0.0,
            reason="tempo",
        ),
        PlannedSession(
            date=date(2026, 1, 2),
            sport="run",
            kind="long",
            title="Long run",
            steps=[],
            duration_min=60,
            distance_m=15000,
            rpe=4.0,
            leg_load=180.0,
            finger_load=0.0,
            reason="long",
        ),
        PlannedSession(
            date=date(2026, 1, 3),
            sport="run",
            kind="intervals",
            title="Intervals",
            steps=[],
            duration_min=40,
            distance_m=7000,
            rpe=8.0,
            leg_load=150.0,
            finger_load=0.0,
            reason="intervals",
        ),
    ]
    violations = check_plan(sessions, None)
    assert violations


def test_generate_race_plan_raises_infeasible_goal() -> None:
    inp = PlanInput(
        distance_km=21.1,
        goal_date=date(2026, 3, 1),
        vdot=49.8,
        runs_per_week=4,
        current_weekly_km=30.0,
    )
    with pytest.raises(InfeasibleGoal, match="later date|weeks|volume"):
        generate_race_plan(inp, date(2026, 1, 1), None)


def test_generate_race_plan_returns_week_sessions() -> None:
    inp = PlanInput(
        distance_km=21.1,
        goal_date=date(2027, 3, 14),
        vdot=49.8,
        runs_per_week=4,
        current_weekly_km=12.0,
    )
    plan = generate_race_plan(inp, date(2027, 1, 1), None)
    assert plan
    assert all(isinstance(session, PlannedSession) for session in plan)
    assert all(session.sport == "run" for session in plan)


def test_scheduled_race_plan_expands_fixed_commitments(tmp_path) -> None:
    inp = PlanInput(
        distance_km=5.0,
        goal_date=date(2026, 12, 28),
        vdot=49.8,
        runs_per_week=2,
        climbing_target_per_week=0,
        current_weekly_km=20.0,
    )
    football = Commitment(
        kind="fixed",
        sport="football",
        title="Football",
        weekday=1,
        start_time="19:00",
        duration_min=90,
        rpe=6.0,
    )

    result = generate_race_plan_result(
        inp,
        date(2026, 11, 9),
        load_config(local_path=tmp_path / "local.toml", env={}),
        commitments=[football],
    )

    assert not result.dropped
    assert any("leg-load cap remains exceeded" in warning for warning in result.warnings)
    assert any("spaced from football" in session.reason for session in result.sessions)
    assert all(session.start_time is not None for session in result.sessions)
