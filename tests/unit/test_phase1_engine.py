from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from runcoach.config import ConfigNode, load_config, load_scheduling_config
from runcoach.domain.models import Commitment, PlanInput, PlannedSession, Step
from runcoach.engine.load import duration_from_distance_and_pace, session_rpe_load
from runcoach.engine.plan import (
    InfeasibleGoal,
    generate_race_plan,
    generate_race_plan_result,
)
from runcoach.engine.progression import split_blocks, weekly_targets
from runcoach.engine.safety import check_plan
from runcoach.engine.templates import WeekContext, build_session, quality_minutes
from runcoach.engine.vdot import vdot_from_race, velocity_at_vo2, vo2_at_velocity
from runcoach.engine.zones import zones_from_vdot

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden_half_plan.json"
MONDAY = date(2026, 11, 16)


def _config() -> ConfigNode:
    return load_config(local_path=Path("does-not-exist.toml"), env={})


CFG = _config()
TYPED = load_scheduling_config(CFG)


def _run(day: date, kind: str, minutes: int, load: float, steps: list[Step] | None = None):
    return PlannedSession(
        date=day,
        start_time="07:00",
        sport="run",
        kind=kind,
        title=kind.title(),
        steps=steps or [],
        duration_min=minutes,
        rpe=4.0,
        leg_load=load,
    )


def test_vdot_values_are_close_to_expected() -> None:
    assert 49.0 < vdot_from_race(5000, 20 * 60) < 50.5
    assert 37.5 < vdot_from_race(5000, 25 * 60) < 39.5
    assert 50.0 < vdot_from_race(10000, 40 * 60) < 53.5


def test_vdot_inverts_vo2() -> None:
    assert abs(velocity_at_vo2(vo2_at_velocity(200.0)) - 200.0) < 1e-6


def test_zones_are_ordered_within_published_bands_and_read_config() -> None:
    zones = zones_from_vdot(50.0, TYPED.zones)
    assert zones.easy.max_s_per_km > zones.easy.min_s_per_km > zones.marathon.min_s_per_km
    assert zones.marathon.min_s_per_km > zones.threshold.min_s_per_km
    assert zones.threshold.min_s_per_km > zones.interval.min_s_per_km
    assert zones.interval.min_s_per_km > zones.repetition.min_s_per_km
    assert 230.0 < zones.interval.min_s_per_km < 245.0  # 3:50-4:05 per km
    assert 255.0 < zones.threshold.min_s_per_km < 265.0  # 4:15-4:25 per km

    faster = TYPED.zones.model_copy(update={"threshold": 0.88})
    assert zones_from_vdot(50.0, faster).threshold.min_s_per_km < zones.threshold.min_s_per_km


def test_weekly_targets_cap_growth_cut_back_and_taper() -> None:
    targets = weekly_targets(20.0, 10, "half", TYPED)
    taper = TYPED.taper_factors["half"]
    assert len(targets) == 10
    assert targets[0] == pytest.approx(22.0)
    assert targets[3] == pytest.approx(targets[2] * 0.8, abs=0.01)  # cut-back week
    assert targets[4] == pytest.approx(targets[2] * 1.1, abs=0.01)  # level did not advance
    peak = max(targets[: -len(taper)])
    assert targets[-len(taper) :] == pytest.approx([peak * f for f in taper], abs=0.01)
    assert max(weekly_targets(60.0, 12, "5k", TYPED)) <= TYPED.peak_weekly_km["5k"]


def test_split_blocks_fill_the_weeks_and_end_with_the_taper() -> None:
    blocks = split_blocks(17, "marathon", TYPED)
    assert sum(block.weeks for block in blocks) == 17
    assert [block.label for block in blocks] == ["base", "build", "peak", "taper"]
    assert blocks[-1].weeks == len(TYPED.taper_factors["marathon"])


def test_load_helpers() -> None:
    assert session_rpe_load(8.0, 30) == 240.0
    assert duration_from_distance_and_pace(1000.0, 300.0) == pytest.approx(5.0, abs=1e-6)


def test_templates_build_structured_sessions_with_zone_paces() -> None:
    zones = zones_from_vdot(50.0, TYPED.zones)
    early = build_session(
        "tempo", WeekContext(MONDAY, quality_minutes("tempo", 0.0, TYPED)), zones, TYPED
    )
    late = build_session(
        "tempo", WeekContext(MONDAY, quality_minutes("tempo", 1.0, TYPED)), zones, TYPED
    )
    assert [step.kind for step in early.steps] == ["warmup", "work", "cooldown"]
    assert early.steps[1].duration_s == 15 * 60 and late.steps[1].duration_s == 40 * 60
    assert early.steps[1].pace_min_s_per_km == zones.threshold.min_s_per_km

    intervals = build_session(
        "intervals",
        WeekContext(MONDAY, quality_minutes("intervals", 1.0, TYPED), progress=1.0),
        zones,
        TYPED,
    )
    repeat = intervals.steps[1]
    assert repeat.kind == "repeat" and repeat.repeats == 6
    assert repeat.children[0].duration_s == 5 * 60
    assert intervals.duration_min == 10 + 6 * 10 + 10

    easy = build_session("easy", WeekContext(MONDAY, 40, strides=True), zones, TYPED)
    assert easy.steps[1].repeats == TYPED.templates.strides_repeats
    assert easy.leg_load == pytest.approx(TYPED.rpe.easy * easy.duration_min)


def test_safety_flags_each_rule_with_a_bad_plan() -> None:
    def kinds(sessions: list[PlannedSession], **kwargs: object) -> set[str]:
        return {v.kind for v in check_plan(sessions, TYPED, **kwargs)}  # type: ignore[arg-type]

    # Hard sessions on consecutive days.
    assert "hard-leg-spacing" in kinds(
        [_run(MONDAY, "tempo", 30, 180), _run(MONDAY + timedelta(days=1), "long", 30, 120)]
    )
    # Weekly load rising 30% against the previous week.
    rise = [_run(MONDAY + timedelta(weeks=w), "easy", 40, load) for w, load in ((0, 100), (1, 130))]
    assert "weekly-rise" in kinds(rise)
    assert "weekly-rise" not in kinds(rise, exempt_load_weeks={MONDAY + timedelta(weeks=1)})
    # Long run above its share of weekly run time.
    assert "long-run-share" in kinds(
        [_run(MONDAY, "long", 90, 360), _run(MONDAY + timedelta(days=3), "easy", 30, 90)]
    )
    # Taper week above the preceding peak.
    taper = [
        _run(MONDAY + timedelta(weeks=w), "easy", 40, load) for w, load in ((0, 100), (1, 105))
    ]
    assert "taper-above-peak" in kinds(taper, taper_start=MONDAY + timedelta(weeks=1))
    # Easy pace faster than threshold pace.
    easy = Step(kind="steady", duration_s=1800, pace_min_s_per_km=240, pace_max_s_per_km=250)
    tempo = Step(kind="work", duration_s=1200, pace_min_s_per_km=260, pace_max_s_per_km=260)
    assert "pace-order" in kinds(
        [
            _run(MONDAY, "easy", 30, 90, [easy]),
            _run(MONDAY + timedelta(days=3), "tempo", 20, 120, [tempo]),
        ]
    )


def test_infeasible_goals_explain_and_suggest_a_date() -> None:
    too_soon = PlanInput(
        distance="half", goal_date=date(2027, 1, 10), vdot=49.8, current_weekly_km=30
    )
    with pytest.raises(InfeasibleGoal, match=r"at least 10 weeks.*2027-01-24"):
        generate_race_plan(too_soon, MONDAY, CFG)
    no_base = PlanInput(
        distance="marathon", goal_date=date(2027, 6, 1), vdot=45, current_weekly_km=0
    )
    with pytest.raises(InfeasibleGoal, match="above the 10% weekly cap"):
        generate_race_plan(no_base, MONDAY, CFG)


def test_race_plan_has_paced_steps_tapers_and_stops_before_race_day() -> None:
    inp = PlanInput(
        distance="half",
        goal_date=date(2027, 3, 14),
        vdot=49.8,
        runs_per_week=4,
        climbing_target_per_week=0,
        current_weekly_km=30,
    )
    result = generate_race_plan_result(inp, date(2026, 11, 18), CFG)  # a Wednesday

    assert not result.violations
    assert min(s.date for s in result.sessions) >= date(2026, 11, 23)  # next Monday
    assert max(s.date for s in result.sessions) < inp.goal_date
    assert all(s.steps and s.distance_m for s in result.sessions)
    assert {s.kind for s in result.sessions} >= {"easy", "long", "tempo", "intervals"}
    weekly: dict[date, float] = {}
    for s in result.sessions:
        monday = s.date - timedelta(days=s.date.weekday())
        weekly[monday] = weekly.get(monday, 0.0) + (s.distance_m or 0)
    volumes = [weekly[w] for w in sorted(weekly)]
    assert volumes[-1] < volumes[-2] < max(volumes)  # taper


def test_scheduled_race_plan_expands_fixed_commitments() -> None:
    inp = PlanInput(
        distance="5k",
        goal_date=date(2027, 1, 10),
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

    result = generate_race_plan_result(inp, date(2026, 11, 9), CFG, commitments=[football])

    assert not result.dropped
    assert not result.violations
    assert any("spaced from football" in session.reason for session in result.sessions)
    assert all(session.start_time is not None for session in result.sessions)


@given(
    vdot=st.floats(min_value=25, max_value=80),
    runs=st.integers(min_value=2, max_value=6),
    distance=st.sampled_from(["5k", "10k", "half", "marathon"]),
    weeks=st.integers(min_value=6, max_value=30),
    current_km=st.floats(min_value=10, max_value=80),
)
def test_generated_race_plans_are_safe_or_infeasible(
    vdot: float, runs: int, distance: str, weeks: int, current_km: float
) -> None:
    inp = PlanInput(
        distance=distance,  # type: ignore[arg-type]
        goal_date=MONDAY + timedelta(weeks=weeks, days=-1),
        vdot=vdot,
        runs_per_week=runs,
        climbing_target_per_week=0,
        current_weekly_km=current_km,
    )
    try:
        result = generate_race_plan_result(inp, MONDAY, CFG)
    except InfeasibleGoal:
        return
    assert result.violations == []


def test_golden_half_marathon_plan(request: pytest.FixtureRequest) -> None:
    inp = PlanInput(
        distance="half",
        goal_date=date(2027, 3, 14),
        vdot=49.8,
        runs_per_week=4,
        climbing_target_per_week=0,
        current_weekly_km=30,
    )
    plan = [s.model_dump(mode="json") for s in generate_race_plan(inp, MONDAY, CFG)]
    if request.config.getoption("--update-golden"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")
    assert GOLDEN.exists(), "run pytest --update-golden to create the snapshot"
    assert plan == json.loads(GOLDEN.read_text(encoding="utf-8")), (
        "plan output changed; review it, then run pytest --update-golden"
    )
