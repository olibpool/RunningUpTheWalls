from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from alembic.config import Config

from alembic import command
from runcoach.domain.models import Activity, CalendarEvent, PlannedSession

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DIR = ROOT / "alembic"
_DB_PATHS: dict[int, str] = {}


def to_row(model: Activity) -> dict[str, object]:
    """Convert an Activity model into a SQLite-ready row dictionary."""
    rpe_value = model.rpe
    payload: dict[str, object] = {
        "source": model.source,
        "external_id": model.external_id,
        "sport": model.sport,
        "started_at": model.started_at,
        "duration_s": model.duration_s,
        "distance_m": model.distance_m,
        "avg_hr": model.avg_hr,
        "rpe": rpe_value,
        "load": model.load,
        "session_id": model.session_id,
        "raw_path": model.raw_path,
    }
    if payload["load"] is None and rpe_value is not None:
        payload["load"] = float(rpe_value) * (float(model.duration_s) / 60.0)
    return payload


def from_row(row: sqlite3.Row) -> Activity:
    """Convert a SQLite row into an Activity model without leaking row objects."""
    return Activity(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        sport=row["sport"],
        started_at=row["started_at"],
        duration_s=int(row["duration_s"]),
        distance_m=row["distance_m"],
        avg_hr=row["avg_hr"],
        rpe=row["rpe"],
        load=row["load"],
        session_id=row["session_id"],
        raw_path=row["raw_path"],
    )


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else ROOT / "runcoach.db"
    conn = sqlite3.connect(str(path))
    _DB_PATHS[id(conn)] = str(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    migrate(conn)
    return conn


def _alembic_config(db_path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATION_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return config


def migrate(conn: sqlite3.Connection) -> None:
    db_path = _DB_PATHS.get(id(conn))
    if db_path is None:
        return
    command.upgrade(_alembic_config(Path(db_path)), "head")


def insert_activity(conn: sqlite3.Connection, activity: Activity) -> int:
    """Insert an activity row, returning the existing row id when duplicated."""
    if activity.source and activity.external_id:
        existing = conn.execute(
            "SELECT id FROM activity WHERE source = ? AND external_id = ?",
            (activity.source, activity.external_id),
        ).fetchone()
        if existing is not None:
            return int(existing["id"])

    payload = to_row(activity)
    if payload["load"] is None:
        payload["load"] = 0.0

    with conn:
        cursor = conn.execute(
            """
            INSERT INTO activity (
                source, external_id, sport, started_at, duration_s,
                distance_m, avg_hr, rpe, load, session_id, raw_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["source"],
                payload["external_id"],
                payload["sport"],
                payload["started_at"],
                payload["duration_s"],
                payload["distance_m"],
                payload["avg_hr"],
                payload["rpe"],
                payload["load"],
                payload["session_id"],
                payload["raw_path"],
            ),
        )
        inserted_id = cursor.lastrowid
        if inserted_id is None:
            raise RuntimeError("Failed to insert activity row")
        return int(inserted_id)


def apply_changes(
    conn: sqlite3.Connection,
    changes: list[object],
) -> list[int]:
    """Persist rule-based session changes and adjustment log entries in one transaction."""
    stored_ids: list[int] = []
    with conn:
        for change in changes:
            action = getattr(change, "action", "modify")
            session_id = getattr(change, "session_id", None)
            updates = getattr(change, "updates", {}) or {}
            rule = getattr(change, "rule", "unknown")
            reason = getattr(change, "reason", "")
            if action == "modify" and session_id is not None:
                if updates:
                    assignments = ", ".join(f"{key} = ?" for key in updates)
                    params = [value for value in updates.values()] + [session_id]
                    conn.execute(f"UPDATE session SET {assignments} WHERE id = ?", params)
            elif action == "drop" and session_id is not None:
                conn.execute("UPDATE session SET status = 'dropped' WHERE id = ?", (session_id,))
            elif action == "add":
                row = {
                    "date": updates.get("date"),
                    "sport": updates.get("sport", "run"),
                    "kind": updates.get("kind", "easy"),
                    "title": updates.get("title", "Added session"),
                    "steps_json": json.dumps(
                        updates.get("steps", []),
                        separators=(",", ":"),
                    ),
                    "status": "planned",
                }
                cursor = conn.execute(
                    """
                    INSERT INTO session (
                        plan_id, date, sport, kind, title, steps_json, planned_duration_min,
                        planned_distance_m, planned_rpe, leg_load, finger_load, status, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        updates.get("plan_id", 1),
                        row["date"],
                        row["sport"],
                        row["kind"],
                        row["title"],
                        row["steps_json"],
                        updates.get("duration_min"),
                        updates.get("distance_m"),
                        updates.get("rpe"),
                        updates.get("leg_load", 0.0),
                        updates.get("finger_load", 0.0),
                        row["status"],
                        reason,
                    ),
                )
                if cursor.lastrowid is not None:
                    stored_ids.append(int(cursor.lastrowid))

            conn.execute(
                """
                INSERT INTO adjustment (
                    created_at, session_id, rule, before_json, after_json, reason
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    session_id,
                    rule,
                    (
                        json.dumps(
                            getattr(change, "before_json", None),
                            separators=(",", ":"),
                        )
                        if getattr(change, "before_json", None) is not None
                        else None
                    ),
                    (
                        json.dumps(
                            getattr(change, "after_json", None),
                            separators=(",", ":"),
                        )
                        if getattr(change, "after_json", None) is not None
                        else None
                    ),
                    reason,
                ),
            )
    return stored_ids


def save_commitment(conn: sqlite3.Connection, commitment: object) -> int:
    """Persist a commitment row and return its primary-key id."""
    if not hasattr(commitment, "title"):
        raise TypeError("commitment must have a title")

    with conn:
        rpe_raw: object = getattr(commitment, "rpe", None)
        duration_value = getattr(commitment, "duration_min", None)
        rpe_value = None
        if isinstance(rpe_raw, (int, float, str)):
            rpe_value = float(rpe_raw)
        cursor = conn.execute(
            """
            INSERT INTO commitment (
                kind, sport, title, weekday, start_date, end_date,
                start_time, duration_min, rpe, severity, source,
                external_id, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                getattr(commitment, "kind", "fixed"),
                getattr(commitment, "sport", None),
                getattr(commitment, "title", ""),
                getattr(commitment, "weekday", None),
                getattr(commitment, "start_date", None),
                getattr(commitment, "end_date", None),
                getattr(commitment, "start_time", None),
                duration_value,
                rpe_value,
                getattr(commitment, "severity", None),
                getattr(commitment, "source", "manual"),
                getattr(commitment, "external_id", None),
                1 if getattr(commitment, "active", True) else 0,
            ),
        )
        last_id = cursor.lastrowid
        if last_id is None:
            raise RuntimeError("Failed to insert commitment")
        return int(last_id)


def list_commitments(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """Return all commitment rows as dictionaries."""
    rows = conn.execute(
        """
        SELECT id, kind, sport, title, weekday, start_date, end_date, start_time,
               duration_min, rpe, severity, source, external_id, active
        FROM commitment
        ORDER BY id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def delete_commitment(conn: sqlite3.Connection, commitment_id: int) -> None:
    """Delete a commitment row by id."""
    conn.execute("DELETE FROM commitment WHERE id = ?", (commitment_id,))


def cache_calendar_events(
    conn: sqlite3.Connection,
    events: list[CalendarEvent],
    *,
    window_start: date,
    window_end: date,
) -> None:
    """Upsert fetched calendar occurrences and remove stale rows from their date window."""
    if window_end < window_start:
        raise ValueError("window_end must not precede window_start")
    fetched_at = datetime.now(timezone.utc).isoformat()
    with conn:
        for event in events:
            conn.execute(
                """
                INSERT INTO calendar_event (
                    external_id, title, starts_at, ends_at, all_day, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(external_id) DO UPDATE SET
                    title = excluded.title,
                    starts_at = excluded.starts_at,
                    ends_at = excluded.ends_at,
                    all_day = excluded.all_day,
                    fetched_at = excluded.fetched_at
                """,
                (
                    event.external_id,
                    event.title,
                    event.starts_at,
                    event.ends_at,
                    int(event.all_day),
                    event.fetched_at or fetched_at,
                ),
            )

        external_ids = [event.external_id for event in events]
        if external_ids:
            placeholders = ", ".join("?" for _ in external_ids)
            conn.execute(
                f"""
                DELETE FROM calendar_event
                WHERE substr(starts_at, 1, 10) BETWEEN ? AND ?
                  AND external_id NOT IN ({placeholders})
                """,
                (window_start.isoformat(), window_end.isoformat(), *external_ids),
            )
        else:
            conn.execute(
                """
                DELETE FROM calendar_event
                WHERE substr(starts_at, 1, 10) BETWEEN ? AND ?
                """,
                (window_start.isoformat(), window_end.isoformat()),
            )


def save_plan(
    conn: sqlite3.Connection,
    sessions: list[PlannedSession],
    *,
    mode: str = "race",
    goal_distance_m: int | float | None = None,
    goal_date: date | str | None = None,
    config_snapshot: str | dict[str, object] = "{}",
) -> int:
    if not sessions:
        raise ValueError("At least one session is required to save a plan")

    plan_snapshot = (
        config_snapshot if isinstance(config_snapshot, str) else json.dumps(config_snapshot)
    )
    goal_date_value = goal_date.isoformat() if isinstance(goal_date, date) else goal_date
    start_date_value = min(session.date.isoformat() for session in sessions)
    created_at = datetime.now(timezone.utc).isoformat()

    with conn:
        cursor = conn.execute(
            """
            INSERT INTO plan (
                mode, goal_distance_m, goal_date, start_date,
                status, created_at, config_snapshot
            )
            VALUES (?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                mode,
                int(goal_distance_m) if goal_distance_m is not None else None,
                goal_date_value,
                start_date_value,
                created_at,
                plan_snapshot,
            ),
        )
        last_row_id = cursor.lastrowid
        if last_row_id is None:
            raise RuntimeError("Failed to insert plan row")
        plan_id = int(last_row_id)

        for session in sessions:
            conn.execute(
                """
                INSERT INTO session (
                    plan_id, date, start_time, sport, kind, title, steps_json,
                    planned_duration_min, planned_distance_m, planned_rpe,
                    leg_load, finger_load, status, reason,
                    garmin_workout_id, garmin_schedule_id, gcal_event_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, NULL, NULL, NULL)
                """,
                (
                    plan_id,
                    session.date.isoformat(),
                    None,
                    session.sport,
                    session.kind,
                    session.title,
                    json.dumps(
                        [step.model_dump(mode="json") for step in session.steps],
                        separators=(",", ":"),
                    ),
                    session.duration_min,
                    session.distance_m,
                    session.rpe,
                    session.leg_load,
                    session.finger_load,
                    session.reason,
                ),
            )
    return plan_id


__all__ = [
    "connect",
    "migrate",
    "save_plan",
    "insert_activity",
    "from_row",
    "to_row",
    "apply_changes",
    "save_commitment",
    "list_commitments",
    "delete_commitment",
    "cache_calendar_events",
]
