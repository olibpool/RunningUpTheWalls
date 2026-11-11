from __future__ import annotations

from datetime import date, timedelta

import pytest

from runcoach.config import load_config, load_scheduling_config
from runcoach.domain.models import Checkin, Commitment
from runcoach.engine.events import CommitmentOccurrence, day_constraints, expand_commitments


@pytest.mark.parametrize(("severity", "following_days"), [(1, 1), (2, 1), (3, 2)])
def test_penalty_applies_easy_constraint_for_severity_window(
    severity: int,
    following_days: int,
) -> None:
    event_day = date(2026, 11, 14)
    event = CommitmentOccurrence(
        commitment_id=1,
        title="Drinks",
        sport=None,
        kind="penalty",
        date=event_day,
        start_time=None,
        duration_min=None,
        rpe=None,
        severity=severity,
    )

    constraints = day_constraints([event], [], load_scheduling_config(load_config(env={})))

    for offset in range(following_days + 1):
        constraint = constraints[event_day + timedelta(days=offset)]
        assert constraint.max_intensity == "easy"
        assert constraint.max_rpe == 4.0
    assert event_day + timedelta(days=following_days + 1) not in constraints


def test_block_expansion_blocks_every_inclusive_date() -> None:
    block = Commitment(
        kind="block",
        title="Holiday",
        start_date="2026-12-20",
        end_date="2026-12-22",
    )
    events = expand_commitments([block], date(2026, 12, 20), date(2026, 12, 22))

    constraints = day_constraints(events, [], load_scheduling_config(load_config(env={})))

    assert len(constraints) == 3
    assert {item.max_intensity for item in constraints.values()} == {"blocked"}


def test_credit_load_and_checkin_easy_constraint() -> None:
    config = load_scheduling_config(load_config(env={}))
    events = [
        CommitmentOccurrence(
            commitment_id=1,
            title="Hike",
            sport="other",
            kind="credit",
            date=date(2026, 11, 14),
            start_time=None,
            duration_min=60,
            rpe=7.0,
        ),
        CommitmentOccurrence(
            commitment_id=2,
            title="Walk",
            sport="other",
            kind="credit",
            date=date(2026, 11, 15),
            start_time=None,
            duration_min=30,
            rpe=5.0,
        ),
    ]
    checkins = [Checkin(date=date(2026, 11, 16), niggle="calf")]

    constraints = day_constraints(events, checkins, config)

    assert constraints[date(2026, 11, 14)].max_intensity == "easy"
    assert constraints[date(2026, 11, 14)].credit_leg_load == 336.0
    assert constraints[date(2026, 11, 14)].easy_sports == frozenset({"run"})
    assert constraints[date(2026, 11, 15)].max_intensity == "normal"
    assert constraints[date(2026, 11, 15)].credit_leg_load == 120.0
    assert constraints[date(2026, 11, 16)].max_intensity == "easy"
    assert constraints[date(2026, 11, 16)].max_rpe == config.penalty.day_of_max_rpe


def test_constraints_merge_with_block_taking_precedence() -> None:
    day = date(2026, 11, 14)
    events = [
        CommitmentOccurrence(
            commitment_id=1,
            title="Drinks",
            sport=None,
            kind="penalty",
            date=day,
            start_time=None,
            duration_min=None,
            rpe=None,
            severity=1,
        ),
        CommitmentOccurrence(
            commitment_id=2,
            title="Holiday",
            sport=None,
            kind="block",
            date=day,
            start_time=None,
            duration_min=None,
            rpe=None,
        ),
    ]

    constraint = day_constraints(events, [], load_scheduling_config(load_config(env={})))[day]

    assert constraint.max_intensity == "blocked"
    assert constraint.sources == frozenset({"penalty", "block"})
    assert "Drinks" in constraint.reason
    assert "Holiday" in constraint.reason
