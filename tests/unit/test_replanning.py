from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from configs import defaults

from runcoach import db
from runcoach.domain.models import Athlete, PlannedSession
from runcoach.engine.continuous import History, WeekLoad
from runcoach.engine.replan import should_replan
from runcoach.sync import replan_continuous_if_due


def test_replan_gate_runs_on_mondays_or_after_interval() -> None:
    config = defaults()
    last_replanned = "2026-11-04T10:00:00+00:00"

    assert should_replan(
        date(2026, 11, 9),
        datetime(2026, 11, 9, 10, tzinfo=timezone.utc),
        last_replanned,
        config,
    )
    assert should_replan(
        date(2026, 11, 11),
        datetime(2026, 11, 11, 10, tzinfo=timezone.utc),
        last_replanned,
        config,
    )
    assert not should_replan(
        date(2026, 11, 8),
        datetime(2026, 11, 8, 10, tzinfo=timezone.utc),
        last_replanned,
        config,
    )
    # Already re-planned this Monday: later syncs the same day must not re-plan again.
    assert not should_replan(
        date(2026, 11, 9),
        datetime(2026, 11, 9, 10, 30, tzinfo=timezone.utc),
        "2026-11-09T10:00:00+00:00",
        config,
    )


def test_replan_gate_requires_valid_utc_timestamp() -> None:
    config = defaults()
    now = datetime(2026, 11, 11, 10, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="UTC"):
        should_replan(date(2026, 11, 11), now, "2026-11-04T10:00:00+01:00", config)
    with pytest.raises(ValueError, match="UTC"):
        should_replan(
            date(2026, 11, 11),
            datetime(2026, 11, 11, 10),
            None,
            config,
        )


def test_replan_gate_honors_configured_interval() -> None:
    config = defaults()
    config.continuous.replan_interval_days = 3
    last = datetime(2026, 11, 9, 10, tzinfo=timezone.utc)  # a Monday re-plan

    assert not should_replan(
        date(2026, 11, 12),
        last + timedelta(days=2, hours=23),
        last.isoformat(),
        config,
    )
    assert should_replan(
        date(2026, 11, 12),
        last + timedelta(days=3),
        last.isoformat(),
        config,
    )
    # A missed Monday is caught by the first sync later that week.
    assert should_replan(
        date(2026, 11, 18),
        datetime(2026, 11, 18, 9, tzinfo=timezone.utc),
        "2026-11-12T10:00:00+00:00",
        config,
    )


def test_sync_replans_when_due_and_skips_recent_non_monday_runs(tmp_path) -> None:
    conn = db.connect(tmp_path / "sync-replan.db")
    today = date(2026, 11, 9)
    config = defaults()
    config.continuous.weeks_ahead = 1
    plan_id = db.save_plan(
        conn,
        [
            PlannedSession(
                date=today,
                sport="run",
                kind="easy",
                title="Existing",
                duration_min=30,
            )
        ],
        mode="continuous",
    )
    athlete = Athlete(
        vdot=45.0,
        vdot_updated_at="2026-11-02T00:00:00+00:00",
        runs_per_week=3,
        climbing_target_per_week=0,
        timezone="UTC",
    )
    history = History(
        weekly_loads=[
            WeekLoad(date(2026, 10, 12), 280.0),
            WeekLoad(date(2026, 10, 19), 300.0),
            WeekLoad(date(2026, 10, 26), 320.0),
            WeekLoad(date(2026, 11, 2), 340.0),
        ],
        typical_easy_duration_min=45,
    )
    monday_time = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)

    result = replan_continuous_if_due(
        conn, plan_id, athlete, history, today, monday_time, [], config
    )

    assert result is not None
    assert db.get_kv(conn, "last_replanned_at") == monday_time.isoformat()
    session_count = conn.execute(
        "SELECT COUNT(*) FROM session WHERE plan_id = ?", (plan_id,)
    ).fetchone()[0]
    later_result = replan_continuous_if_due(
        conn,
        plan_id,
        athlete,
        history,
        date(2026, 11, 11),
        datetime(2026, 11, 11, 12, tzinfo=timezone.utc),
        [],
        config,
    )
    assert later_result is None
    assert (
        conn.execute("SELECT COUNT(*) FROM session WHERE plan_id = ?", (plan_id,)).fetchone()[0]
        == session_count
    )
    conn.close()
