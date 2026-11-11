from __future__ import annotations

from datetime import date

from runcoach.adapt.rules import PlanState, evaluate, missed_session_adjustment
from runcoach.domain.models import Activity, Checkin, PlannedSession
from runcoach.ingest.matcher import match, match_activity_to_session


def test_match_activity_to_session_prefers_same_day_run() -> None:
    sessions = [
        PlannedSession(
            date=date(2026, 11, 3),
            sport="run",
            kind="easy",
            title="Easy run",
            steps=[],
            duration_min=35,
            distance_m=5000,
            leg_load=40.0,
            finger_load=0.0,
            reason="steady easy",
        ),
        PlannedSession(
            date=date(2026, 11, 5),
            sport="run",
            kind="long",
            title="Long run",
            steps=[],
            duration_min=60,
            distance_m=9000,
            leg_load=120.0,
            finger_load=0.0,
            reason="long run",
        ),
    ]
    activity = Activity(
        source="gpx",
        sport="run",
        started_at="2026-11-03T07:00:00+00:00",
        duration_s=1800,
        distance_m=4800.0,
    )

    matched = match_activity_to_session(activity, sessions)

    assert matched is not None
    assert matched.date == date(2026, 11, 3)
    assert matched.kind == "easy"


def test_match_function_prefers_same_day_candidate() -> None:
    sessions = [
        PlannedSession(
            date=date(2026, 11, 3),
            sport="run",
            kind="easy",
            title="Easy run",
            steps=[],
            duration_min=30,
            distance_m=5000,
            leg_load=60.0,
            finger_load=0.0,
            reason="easy",
        ),
        PlannedSession(
            date=date(2026, 11, 5),
            sport="run",
            kind="long",
            title="Long run",
            steps=[],
            duration_min=60,
            distance_m=9000,
            leg_load=180.0,
            finger_load=0.0,
            reason="long",
        ),
    ]
    activity = Activity(
        source="gpx",
        sport="run",
        started_at="2026-11-03T07:00:00+00:00",
        duration_s=1800,
        distance_m=4800.0,
    )

    matches = match([activity], sessions, None)
    assert len(matches) == 1
    assert matches[0].session.date == date(2026, 11, 3)


def test_evaluate_reports_missed_session_and_ill() -> None:
    planned = PlannedSession(
        date=date(2026, 11, 4),
        sport="run",
        kind="tempo",
        title="Tempo run",
        steps=[],
        duration_min=40,
        distance_m=7000,
        leg_load=80.0,
        finger_load=0.0,
        reason="tempo",
    )
    state = PlanState(
        sessions=[planned],
        activities=[],
        checkins=[Checkin(date=date(2026, 11, 4), ill=True, niggle="tight calves")],
        athlete={"vdot": 50.0, "runs_per_week": 4, "climbing_target_per_week": 2},
    )

    changes = evaluate(state, date(2026, 11, 6), None)
    assert any(change.rule == "missed_session" for change in changes)
    assert any(change.rule == "ill_or_niggle" for change in changes)


def test_missed_session_rule_builds_adjustment_reason() -> None:
    planned = PlannedSession(
        date=date(2026, 11, 4),
        sport="run",
        kind="tempo",
        title="Tempo run",
        steps=[],
        duration_min=40,
        distance_m=7000,
        leg_load=80.0,
        finger_load=0.0,
        reason="tempo",
    )

    adjustment = missed_session_adjustment(planned)

    assert adjustment.rule == "missed_session"
    assert "do not cram" in adjustment.reason.lower()
    assert adjustment.session_id is None
