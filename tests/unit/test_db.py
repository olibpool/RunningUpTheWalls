import sqlite3
from datetime import date

import pytest

from runcoach import db
from runcoach.adapt.rules import Change
from runcoach.domain.models import Activity, CalendarEvent, Commitment, PlannedSession
from runcoach.engine.events import expand_commitments
from runcoach.ingest.calendar import busy_slots, parse_ical_events


def test_db_save_plan_persists_sessions(tmp_path) -> None:
    conn = db.connect(tmp_path / "coach.db")
    plan_id = db.save_plan(
        conn,
        [
            PlannedSession(
                date=date(2027, 1, 10),
                sport="run",
                kind="long",
                title="Long run",
                steps=[],
                duration_min=60,
                distance_m=10000,
                rpe=4.0,
                leg_load=180.0,
                finger_load=0.0,
                reason="long run",
            )
        ],
        goal_distance_m=10000,
        goal_date=date(2027, 3, 14),
    )

    assert plan_id > 0
    assert conn.execute("SELECT COUNT(*) FROM plan WHERE id = ?", (plan_id,)).fetchone()[0] == 1
    assert (
        conn.execute("SELECT COUNT(*) FROM session WHERE plan_id = ?", (plan_id,)).fetchone()[0]
        == 1
    )
    conn.close()


def test_db_migration_and_fk_error(tmp_path) -> None:
    db_path = tmp_path / "coach.db"
    conn = db.connect(db_path)
    assert (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        is not None
    )
    conn.close()

    conn2 = db.connect(db_path)
    conn2.execute("SELECT 1")
    conn2.close()

    conn3 = db.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn3.execute(
            "INSERT INTO session (plan_id, date, sport, kind, title, steps_json, status) "
            "VALUES (999, '2026-11-10', 'run', 'easy', 'Bad', '[]', 'planned')"
        )
    conn3.close()


def test_insert_activity_is_idempotent(tmp_path) -> None:
    conn = db.connect(tmp_path / "activities.db")
    activity = Activity(
        source="gpx",
        external_id="abc123",
        sport="run",
        started_at="2026-11-02T06:30:00+00:00",
        duration_s=1800,
        distance_m=5000.0,
        avg_hr=150,
        rpe=7.0,
        load=None,
        session_id=None,
        raw_path=None,
    )

    first_id = db.insert_activity(conn, activity)
    second_id = db.insert_activity(conn, activity)

    assert first_id == second_id
    assert conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 1
    row = conn.execute(
        "SELECT load FROM activity WHERE source = ? AND external_id = ?",
        ("gpx", "abc123"),
    ).fetchone()
    assert row is not None
    assert row[0] == 210.0
    conn.close()


def test_apply_changes_updates_session_and_logs_adjustment(tmp_path) -> None:
    conn = db.connect(tmp_path / "changes.db")
    plan_id = db.save_plan(
        conn,
        [
            PlannedSession(
                date=date(2027, 1, 10),
                sport="run",
                kind="tempo",
                title="Tempo run",
                steps=[],
                duration_min=40,
                distance_m=7000,
                rpe=6.0,
                leg_load=240.0,
                finger_load=0.0,
                reason="tempo",
            )
        ],
        goal_distance_m=10000,
        goal_date=date(2027, 3, 14),
    )
    session_id = conn.execute(
        "SELECT id FROM session WHERE plan_id = ?",
        (plan_id,),
    ).fetchone()[0]

    db.apply_changes(
        conn,
        [
            Change(
                session_id=session_id,
                action="modify",
                updates={"status": "skipped", "reason": "illness"},
                rule="ill_or_niggle",
                reason="Illness flagged; keep recovery week.",
            )
        ],
    )

    assert (
        conn.execute("SELECT status FROM session WHERE id = ?", (session_id,)).fetchone()[0]
        == "skipped"
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM adjustment WHERE session_id = ? AND rule = ?",
            (session_id, "ill_or_niggle"),
        ).fetchone()[0]
        == 1
    )
    conn.close()


def test_expand_commitments_and_commitment_roundtrip(tmp_path) -> None:
    commitments = [
        Commitment(
            kind="fixed",
            title="Football",
            sport="football",
            weekday=0,
            start_time="19:00",
            duration_min=90,
            rpe=6.0,
            active=True,
        ),
        Commitment(
            kind="block",
            title="Holiday",
            start_date="2026-11-15",
            end_date="2026-11-18",
            active=True,
        ),
    ]
    occurrences = expand_commitments(commitments, date(2026, 11, 9), date(2026, 11, 25))

    assert any(
        item.title == "Football" and item.date.isoformat() == "2026-11-09" for item in occurrences
    )
    assert any(
        item.title == "Holiday" and item.date.isoformat() == "2026-11-15" for item in occurrences
    )

    conn = db.connect(tmp_path / "commitments.db")
    commitment_id = db.save_commitment(
        conn,
        Commitment(
            kind="fixed",
            title="Football",
            sport="football",
            weekday=2,
            start_time="18:30",
            duration_min=75,
            active=True,
        ),
    )
    assert commitment_id > 0
    rows = db.list_commitments(conn)
    assert any(row["title"] == "Football" for row in rows)
    db.delete_commitment(conn, commitment_id)
    assert not any(row["id"] == commitment_id for row in db.list_commitments(conn))
    conn.close()


def test_parse_ical_events_and_busy_slots() -> None:
    ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:evt-1
DTSTAMP:20261101T100000Z
DTSTART:20261115T090000Z
DTEND:20261115T103000Z
SUMMARY:Team run
END:VEVENT
BEGIN:VEVENT
UID:evt-2
DTSTAMP:20261101T100000Z
DTSTART:20261116T100000Z
DTEND:20261116T113000Z
SUMMARY:Client meeting
END:VEVENT
END:VCALENDAR
"""
    events = parse_ical_events(ics)
    assert len(events) == 2
    assert events[0].title == "Team run"
    assert events[0].starts_at == "2026-11-15T09:00:00+00:00"

    busy = busy_slots(
        [
            CalendarEvent(
                external_id="evt-1",
                title="Team run",
                starts_at="2026-11-15T09:00:00+00:00",
                ends_at="2026-11-15T10:30:00+00:00",
                all_day=False,
                fetched_at="2026-11-01T10:00:00+00:00",
            ),
            CalendarEvent(
                external_id="evt-2",
                title="Client meeting",
                starts_at="2026-11-16T10:00:00+00:00",
                ends_at="2026-11-16T11:30:00+00:00",
                all_day=False,
                fetched_at="2026-11-01T10:00:00+00:00",
            ),
        ],
        start="2026-11-15T08:00:00+00:00",
        end="2026-11-16T12:00:00+00:00",
    )
    assert busy[0][0].isoformat() == "2026-11-15T09:00:00+00:00"
    assert busy[0][1].isoformat() == "2026-11-15T10:30:00+00:00"
