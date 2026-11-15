from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from runcoach.adapt.rules import (
    PlanState,
    evaluate,
    ill_or_niggle,
    missed_once,
    missed_two_plus,
    retest_suggested,
)
from runcoach.config import load_config, load_scheduling_config
from runcoach.domain.models import Activity, Checkin, Session, Step
from runcoach.ingest.matcher import match

CFG = load_scheduling_config(load_config(local_path=Path("does-not-exist.toml"), env={}))
MONDAY = date(2026, 11, 2)


def _session(
    sid: int,
    day: date,
    kind: str = "easy",
    minutes: int = 40,
    status: str = "planned",
    sport: str = "run",
    load: float = 120.0,
) -> Session:
    return Session(
        id=sid,
        plan_id=1,
        status=status,  # type: ignore[arg-type]
        date=day,
        sport=sport,  # type: ignore[arg-type]
        kind=kind,
        title=kind.title(),
        steps=[Step(kind="steady", duration_s=minutes * 60, pace_min_s_per_km=300)],
        duration_min=minutes,
        distance_m=minutes * 200,
        rpe=6.0 if kind in {"tempo", "intervals"} else 3.0,
        leg_load=load,
    )


def _activity(aid: int, started: str, minutes: int, sport: str = "run") -> Activity:
    return Activity(id=aid, source="gpx", sport=sport, started_at=started, duration_s=minutes * 60)


def test_matcher_same_day_one_day_off_ties_and_wrong_sport() -> None:
    sessions = [
        _session(1, MONDAY, minutes=40),
        _session(2, MONDAY + timedelta(days=2), minutes=60),
        _session(3, MONDAY + timedelta(days=4), minutes=30),
        _session(4, MONDAY + timedelta(days=4), minutes=30),
        _session(5, MONDAY + timedelta(days=6), sport="climb", kind="climb_power"),
    ]
    activities = [
        _activity(10, "2026-11-02T07:00:00+00:00", 42),  # same day as session 1
        _activity(11, "2026-11-05T07:00:00+00:00", 58),  # a day after session 2
        _activity(12, "2026-11-06T07:00:00+00:00", 30),  # ties sessions 3 and 4
        _activity(13, "2026-11-08T07:00:00+00:00", 40),  # a run on the climbing day
    ]

    matches = {(m.activity_id, m.session_id) for m in match(activities, sessions, CFG.matching)}

    assert matches == {(10, 1), (11, 2)}


def test_matcher_uses_the_local_day() -> None:
    # 23:30 UTC on 1 Nov is still 1 Nov in London (GMT); 23:30 UTC on 1 Jul is 2 Jul (BST).
    winter = _activity(1, "2026-11-01T23:30:00+00:00", 40)
    summer = _activity(2, "2026-07-01T23:30:00+00:00", 40)
    sessions = [_session(1, date(2026, 11, 1)), _session(2, date(2026, 7, 2))]
    assert [
        (m.activity_id, m.session_id) for m in match([winter, summer], sessions, CFG.matching)
    ] == [
        (2, 2),
        (1, 1),
    ]


def test_missed_once_skips_past_planned_sessions_only() -> None:
    state = PlanState(
        sessions=[
            _session(1, MONDAY),
            _session(2, MONDAY, status="done"),
            _session(3, MONDAY + timedelta(days=1)),
        ],
        activities=[],
        checkins=[],
    )
    changes = missed_once(state, MONDAY + timedelta(days=1))
    assert [(c.session_id, c.updates) for c in changes] == [(1, {"status": "skipped"})]


def test_two_missed_cap_next_week_once() -> None:
    week2 = MONDAY + timedelta(days=7)
    sessions = [
        _session(1, MONDAY, load=100),
        _session(2, MONDAY + timedelta(days=2), load=100),
        _session(3, MONDAY + timedelta(days=4), status="done", load=100),
        _session(4, week2, load=200),
        _session(5, week2 + timedelta(days=2), load=200),
    ]
    state = PlanState(sessions=sessions, activities=[], checkins=[])

    changes = missed_two_plus(state, week2)

    assert {c.session_id for c in changes} == {4, 5}
    assert sum(float(c.updates["leg_load"]) for c in changes) == 300  # last week's planned 300
    already = PlanState(
        sessions=sessions, activities=[], checkins=[], applied=frozenset({(4, "missed_two_plus")})
    )
    assert missed_two_plus(already, week2) == []


def test_illness_runs_by_feel_downgrades_quality_then_ramps() -> None:
    ill_day = MONDAY
    sessions = [
        _session(1, ill_day + timedelta(days=1), kind="tempo", load=240),
        _session(2, ill_day + timedelta(days=13), kind="easy"),
        _session(3, ill_day + timedelta(days=14), kind="easy", load=100),  # first week after
        _session(4, ill_day + timedelta(days=21), kind="easy", load=100),  # second week after
        _session(5, ill_day + timedelta(days=28), kind="easy", load=100),  # back to normal
    ]
    state = PlanState(sessions=sessions, activities=[], checkins=[Checkin(date=ill_day, ill=True)])

    changes = {c.session_id: c for c in ill_or_niggle(state, CFG)}

    tempo = changes[1].updates
    assert tempo["kind"] == "easy" and "(by feel)" in str(tempo["title"])
    steps = tempo["steps"]
    assert isinstance(steps, list) and steps[0].pace_min_s_per_km is None
    assert changes[2].rule == "ill_or_niggle"
    assert changes[3].rule == "illness_ramp" and changes[3].updates["leg_load"] == 70
    assert changes[4].updates["leg_load"] == 85
    assert 5 not in changes


def test_niggle_window_is_shorter() -> None:
    state = PlanState(
        sessions=[_session(1, MONDAY + timedelta(days=2)), _session(2, MONDAY + timedelta(days=3))],
        activities=[],
        checkins=[Checkin(date=MONDAY, niggle="calf")],
    )
    by_feel = [c.session_id for c in ill_or_niggle(state, CFG) if c.rule == "ill_or_niggle"]
    assert by_feel == [1]


def test_retest_suggested_after_three_fast_easy_feeling_quality_runs() -> None:
    sessions = [
        _session(i, MONDAY + timedelta(days=2 * i), kind="tempo", status="done") for i in range(3)
    ]
    planned_pace = 40 * 60 / (40 * 200 / 1000)  # s/km from duration_min and distance_m
    fast = [
        Activity(
            id=10 + s.id,
            source="gpx",
            sport="run",
            started_at=f"{s.date.isoformat()}T07:00:00+00:00",
            duration_s=round(planned_pace * 0.95 * 8),
            distance_m=8000,
            rpe=6.0,
            session_id=s.id,
        )
        for s in sessions
    ]
    state = PlanState(sessions=sessions, activities=fast, checkins=[])

    (flag,) = retest_suggested(state, CFG)
    assert flag.action == "flag" and flag.rule == "retest_suggested"
    assert (
        retest_suggested(PlanState(sessions, fast, [], flags=frozenset({"retest_suggested"})), CFG)
        == []
    )
    hard = [a.model_copy(update={"rpe": 7.0}) for a in fast]
    assert retest_suggested(PlanState(sessions, hard, []), CFG) == []


def test_evaluate_combines_rules() -> None:
    state = PlanState(
        sessions=[_session(1, MONDAY), _session(2, MONDAY + timedelta(days=9))],
        activities=[],
        checkins=[Checkin(date=MONDAY + timedelta(days=8), niggle="knee")],
    )
    rules = [c.rule for c in evaluate(state, MONDAY + timedelta(days=1), CFG)]
    assert rules == ["missed_once", "ill_or_niggle"]
