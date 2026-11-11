from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from runcoach import db
from runcoach.config import load_config
from runcoach.domain.models import Athlete, PlannedSession
from runcoach.engine.continuous import History, WeekLoad
from runcoach.engine.replan import should_replan
from runcoach.sync import replan_continuous_if_due


def test_replan_gate_runs_on_mondays_or_after_interval() -> None:
    config = load_config(env={})
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


def test_replan_gate_requires_valid_utc_timestamp() -> None:
    config = load_config(env={})
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
    config = load_config(env={})
    config.continuous.replan_interval_days = 10
    last = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)

    assert not should_replan(
        date(2026, 11, 10),
        last + timedelta(days=9, hours=23),
        last.isoformat(),
        config,
    )
    assert should_replan(
        date(2026, 11, 11),
        last + timedelta(days=10),
        last.isoformat(),
        config,
    )


def test_sync_replans_when_due_and_skips_recent_non_monday_runs(tmp_path) -> None:
    conn = db.connect(tmp_path / "sync-replan.db")
    today = date(2026, 11, 9)
    config = load_config(env={})
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
