import sqlite3
from datetime import date

import pytest

from runcoach import db
from runcoach.adapt.rules import Change
from runcoach.domain.models import Activity, PlannedSession


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


def test_replace_continuous_sessions_preserves_done_and_logs_replan(tmp_path) -> None:
    conn = db.connect(tmp_path / "replan.db")
    today = date(2026, 11, 9)
    plan_id = db.save_plan(
        conn,
        [
            PlannedSession(
                date=today,
                sport="run",
                kind="easy",
                title="Completed easy",
                duration_min=30,
            ),
            PlannedSession(
                date=date(2026, 11, 10),
                sport="run",
                kind="long",
                title="Old long",
                duration_min=60,
            ),
        ],
        mode="continuous",
    )
    done_id = conn.execute(
        "SELECT id FROM session WHERE plan_id = ? AND title = 'Completed easy'",
        (plan_id,),
    ).fetchone()["id"]
    conn.execute("UPDATE session SET status = 'done' WHERE id = ?", (done_id,))
    conn.commit()
    generated = [
        PlannedSession(
            date=today,
            sport="run",
            kind="easy",
            title="Duplicate completed session",
            duration_min=30,
        ),
        PlannedSession(
            date=date(2026, 11, 11),
            sport="run",
            kind="tempo",
            title="New tempo",
            duration_min=40,
        ),
    ]

    inserted = db.replace_continuous_sessions(
        conn,
        plan_id,
        generated,
        today=today,
        replanned_at="2026-11-09T12:00:00Z",
    )

    assert inserted == 1
    rows = conn.execute(
        "SELECT id, status, title FROM session WHERE plan_id = ? ORDER BY date, id",
        (plan_id,),
    ).fetchall()
    assert [(row["status"], row["title"]) for row in rows] == [
        ("done", "Completed easy"),
        ("planned", "New tempo"),
    ]
    assert db.get_kv(conn, "last_replanned_at") == "2026-11-09T12:00:00+00:00"
    adjustment = conn.execute(
        "SELECT rule FROM adjustment WHERE session_id IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert adjustment is not None
    assert adjustment["rule"] == "weekly_replan"
    conn.close()


def test_db_migration_and_fk_error(tmp_path) -> None:
    db_path = tmp_path / "coach.db"
    conn = db.connect(db_path)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(commitment)").fetchall()}
    assert "minimal_plan" in columns
    latest = max(int(path.name[:4]) for path in db.MIGRATION_DIR.glob("*.sql"))
    assert db.schema_version(conn) == latest
    schema = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    conn.close()

    conn2 = db.connect(db_path)
    assert db.schema_version(conn2) == latest
    assert conn2.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == schema
    conn2.close()

    conn3 = db.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn3.execute(
            "INSERT INTO session (plan_id, date, sport, kind, title, steps_json, status) "
            "VALUES (999, '2026-11-10', 'run', 'easy', 'Bad', '[]', 'planned')"
        )
    conn3.close()


def test_failed_migration_rolls_back_and_keeps_version(tmp_path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_ok.sql").write_text("CREATE TABLE a (id INTEGER PRIMARY KEY);")
    (migrations / "0002_bad.sql").write_text("CREATE TABLE b (id INTEGER);\nNOT VALID SQL;")
    conn = sqlite3.connect(tmp_path / "m.db")

    with pytest.raises(sqlite3.Error):
        db.migrate(conn, migrations)

    assert db.schema_version(conn) == 1
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"a"}
    conn.close()


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


def test_apply_changes_maps_fields_flags_and_rejects_unknown(tmp_path) -> None:
    from runcoach.domain.models import Step

    conn = db.connect(tmp_path / "map.db")
    plan_id = db.save_plan(
        conn, [PlannedSession(date=date(2027, 1, 4), sport="run", kind="tempo", title="T")]
    )
    sid = conn.execute("SELECT id FROM session WHERE plan_id = ?", (plan_id,)).fetchone()[0]
    db.apply_changes(
        conn,
        [
            Change(
                sid,
                "modify",
                {"kind": "easy", "duration_min": 30, "steps": [Step(kind="steady")]},
                "r",
                "why",
            ),
            Change(None, "flag", {"value": "retest please"}, "retest_suggested", "fast"),
        ],
    )
    session = db.get_session(conn, sid)
    assert session is not None and session.kind == "easy" and session.duration_min == 30
    assert session.steps[0].kind == "steady"
    assert db.get_kv(conn, "retest_suggested") == "retest please"
    assert (sid, "r") in db.applied_rules(conn)
    with pytest.raises(ValueError, match="Unknown session fields"):
        db.apply_changes(conn, [Change(sid, "modify", {"nonsense": 1}, "r", "")])
    conn.close()


def test_backup_copies_the_live_database_and_keeps_the_newest(tmp_path) -> None:
    conn = db.connect(tmp_path / "live.db")
    db.set_kv(conn, "marker", "present")
    for stamp in ("20270101T000000Z", "20270102T000000Z", "20270103T000000Z"):
        db.backup(conn, tmp_path / "backups", keep=2, stamp=stamp)
    conn.close()

    kept = sorted(p.name for p in (tmp_path / "backups").iterdir())
    assert kept == ["runcoach-20270102T000000Z.db", "runcoach-20270103T000000Z.db"]
    copy = db.connect(tmp_path / "backups" / kept[-1])
    assert db.get_kv(copy, "marker") == "present"
    copy.close()
