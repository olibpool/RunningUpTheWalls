from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from runcoach.config import load_config
from runcoach.domain.models import (
    Activity,
    Adjustment,
    Athlete,
    CalendarEvent,
    Checkin,
    Commitment,
    GarminPush,
    Plan,
    PlannedSession,
    Session,
)

# All SQL lives in this module behind model-in/model-out functions; that is the seam to swap
# for SQLAlchemy later. Migrations are portable DDL so Alembic can adopt them via a baseline stamp.
ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DIR = ROOT / "migrations"


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
        "laps_json": json.dumps([lap.model_dump() for lap in model.laps]),
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
        laps=json.loads(row["laps_json"]),
    )


def default_db_path() -> Path:
    """The configured database file ([storage] db_path, relative to the project root)."""
    return ROOT / str(load_config().storage.db_path)  # an absolute path replaces ROOT


def backup(conn: sqlite3.Connection, directory: Path, keep: int, stamp: str) -> Path:
    """Copy the live database with SQLite's online backup and keep the newest `keep` copies."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"runcoach-{stamp}.db"
    copy = sqlite3.connect(str(target))
    try:
        conn.backup(copy)
    finally:
        copy.close()
    for old in sorted(directory.glob("runcoach-*.db"))[:-keep]:
        old.unlink()
    return target


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open the database with the required pragmas and apply pending migrations."""
    path = Path(db_path) if db_path is not None else default_db_path()
    # Each connection serves one request or command; the web layer may open it in one worker
    # thread and use it in another, never concurrently.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    """Return the applied migration number stored in PRAGMA user_version."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection, migration_dir: Path = MIGRATION_DIR) -> None:
    """Apply each NNNN_*.sql file above user_version in its own transaction."""
    current = schema_version(conn)
    for path in sorted(migration_dir.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        number = int(path.name[:4])
        if number <= current:
            continue
        script = path.read_text(encoding="utf-8")
        try:
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.rollback()
            raise
        current = number


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
            f"INSERT INTO activity ({', '.join(payload)}) VALUES ({', '.join('?' * len(payload))})",
            tuple(payload.values()),
        )
        inserted_id = cursor.lastrowid
        if inserted_id is None:
            raise RuntimeError("Failed to insert activity row")
        return int(inserted_id)


# PlannedSession field (as used by Change.updates) -> session column.
_SESSION_COLUMNS = {
    "plan_id": "plan_id",
    "status": "status",
    "date": "date",
    "start_time": "start_time",
    "kind": "kind",
    "title": "title",
    "reason": "reason",
    "steps": "steps_json",
    "duration_min": "planned_duration_min",
    "distance_m": "planned_distance_m",
    "rpe": "planned_rpe",
    "leg_load": "leg_load",
    "finger_load": "finger_load",
}


def _column_value(field: str, value: object) -> object:
    if field == "steps" and isinstance(value, list):
        return json.dumps(
            [s.model_dump(mode="json") if hasattr(s, "model_dump") else s for s in value],
            separators=(",", ":"),
        )
    if isinstance(value, date):
        return value.isoformat()
    return value


def apply_changes(conn: sqlite3.Connection, changes: Sequence[object]) -> list[int]:
    """Write rule changes and their adjustment-log rows in one transaction.

    Each change has session_id, action (modify | drop | add | flag), updates (PlannedSession
    field names plus status), rule and reason. Returns the ids of added sessions.
    """
    stored_ids: list[int] = []
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        for change in changes:
            action = getattr(change, "action", "modify")
            session_id = getattr(change, "session_id", None)
            updates: dict[str, object] = getattr(change, "updates", {}) or {}
            rule = getattr(change, "rule", "unknown")
            reason = getattr(change, "reason", "")
            before: dict[str, object] | None = None
            after: dict[str, object] | None = None
            unknown = set(updates) - set(_SESSION_COLUMNS) if action != "flag" else set()
            if unknown:
                raise ValueError(f"Unknown session fields in change: {sorted(unknown)}")
            if action == "modify" and session_id is not None and updates:
                columns = {_SESSION_COLUMNS[f]: _column_value(f, v) for f, v in updates.items()}
                row = conn.execute(
                    f"SELECT {', '.join(columns)} FROM session WHERE id = ?", (session_id,)
                ).fetchone()
                before = dict(row) if row is not None else None
                after = columns
                assignments = ", ".join(f"{column} = ?" for column in columns)
                conn.execute(
                    f"UPDATE session SET {assignments} WHERE id = ?",
                    [*columns.values(), session_id],
                )
            elif action == "drop" and session_id is not None:
                conn.execute("UPDATE session SET status = 'dropped' WHERE id = ?", (session_id,))
            elif action == "flag":
                set_kv(conn, rule, str(updates.get("value", reason)), commit=False)
            elif action == "add":
                columns = {_SESSION_COLUMNS[f]: _column_value(f, v) for f, v in updates.items()}
                columns.setdefault("steps_json", "[]")
                columns["reason"] = reason
                cursor = conn.execute(
                    f"INSERT INTO session ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})",
                    list(columns.values()),
                )
                if cursor.lastrowid is not None:
                    stored_ids.append(int(cursor.lastrowid))
            conn.execute(
                """
                INSERT INTO adjustment
                    (created_at, session_id, rule, before_json, after_json, reason)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    session_id,
                    rule,
                    json.dumps(before) if before is not None else None,
                    json.dumps(after) if after is not None else None,
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
                external_id, active, minimal_plan
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                1 if getattr(commitment, "minimal_plan", False) else 0,
            ),
        )
        last_id = cursor.lastrowid
        if last_id is None:
            raise RuntimeError("Failed to insert commitment")
        return int(last_id)


def list_commitments(conn: sqlite3.Connection) -> list[Commitment]:
    """Return every commitment and event, oldest first."""
    rows = conn.execute("SELECT * FROM commitment ORDER BY id").fetchall()
    return [Commitment.model_validate(dict(row)) for row in rows]


def delete_commitment(conn: sqlite3.Connection, commitment_id: int) -> None:
    """Delete a commitment row by id."""
    with conn:
        conn.execute("DELETE FROM commitment WHERE id = ?", (commitment_id,))


def active_plan(conn: sqlite3.Connection) -> Plan | None:
    """Return the newest active plan, if any."""
    row = conn.execute(
        "SELECT * FROM plan WHERE status = 'active' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return Plan.model_validate(dict(row)) if row is not None else None


def _session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        plan_id=row["plan_id"],
        status=row["status"],
        date=row["date"],
        start_time=row["start_time"],
        sport=row["sport"],
        kind=row["kind"],
        title=row["title"],
        steps=json.loads(row["steps_json"]),
        duration_min=row["planned_duration_min"],
        distance_m=row["planned_distance_m"],
        rpe=row["planned_rpe"],
        leg_load=row["leg_load"],
        finger_load=row["finger_load"],
        reason=row["reason"] or "",
    )


def plan_sessions(
    conn: sqlite3.Connection, plan_id: int, start: date | None = None, end: date | None = None
) -> list[Session]:
    """Return a plan's sessions, optionally limited to dates in [start, end)."""
    rows = conn.execute(
        """
        SELECT * FROM session
        WHERE plan_id = ? AND date >= ? AND date < ?
        ORDER BY date, start_time, id
        """,
        (plan_id, (start or date.min).isoformat(), (end or date.max).isoformat()),
    ).fetchall()
    return [_session(row) for row in rows]


def get_session(conn: sqlite3.Connection, session_id: int) -> Session | None:
    """Return one stored session by id."""
    row = conn.execute("SELECT * FROM session WHERE id = ?", (session_id,)).fetchone()
    return _session(row) if row is not None else None


def session_adjustments(conn: sqlite3.Connection, session_id: int) -> list[Adjustment]:
    """Return the adjustment log for one session, oldest first."""
    rows = conn.execute(
        "SELECT * FROM adjustment WHERE session_id = ? ORDER BY id", (session_id,)
    ).fetchall()
    return [Adjustment.model_validate(dict(row)) for row in rows]


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


def get_kv(conn: sqlite3.Connection, key: str) -> str | None:
    """Return a stored value, or None."""
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else None


def set_kv(conn: sqlite3.Connection, key: str, value: str, *, commit: bool = True) -> None:
    """Store a value; commit=False joins the caller's transaction."""
    sql = "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?"
    if commit:
        with conn:
            conn.execute(sql, (key, value, value))
    else:
        conn.execute(sql, (key, value, value))


def delete_kv(conn: sqlite3.Connection, key: str) -> None:
    """Remove a stored value if present."""
    with conn:
        conn.execute("DELETE FROM kv WHERE key = ?", (key,))


def kv_keys(conn: sqlite3.Connection) -> set[str]:
    """All stored keys."""
    return {str(row["key"]) for row in conn.execute("SELECT key FROM kv").fetchall()}


def activities(conn: sqlite3.Connection, *, unmatched_only: bool = False) -> list[Activity]:
    """Stored activities, oldest first."""
    where = "WHERE session_id IS NULL" if unmatched_only else ""
    rows = conn.execute(f"SELECT * FROM activity {where} ORDER BY started_at, id").fetchall()
    return [from_row(row) for row in rows]


def link_activity(
    conn: sqlite3.Connection, activity_id: int, session_id: int, *, rule: str, reason: str
) -> None:
    """Attach an activity to its session, mark the session done and log why.

    An activity without its own RPE takes the session's planned RPE for its load.
    """
    with conn:
        conn.execute(
            """
            UPDATE activity SET session_id = ?,
                load = COALESCE(rpe, (SELECT planned_rpe FROM session WHERE id = ?), load * 60.0
                    / NULLIF(duration_s, 0)) * duration_s / 60.0
            WHERE id = ?
            """,
            (session_id, session_id, activity_id),
        )
        conn.execute("UPDATE session SET status = 'done' WHERE id = ?", (session_id,))
        conn.execute(
            "INSERT INTO adjustment (created_at, session_id, rule, reason) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), session_id, rule, reason),
        )


def find_activity(conn: sqlite3.Connection, source: str, external_id: str) -> int | None:
    """Id of an already-stored activity, for idempotent ingest."""
    row = conn.execute(
        "SELECT id FROM activity WHERE source = ? AND external_id = ?", (source, external_id)
    ).fetchone()
    return int(row["id"]) if row is not None else None


def calendar_events(conn: sqlite3.Connection) -> list[CalendarEvent]:
    """Every cached calendar occurrence, in start order."""
    rows = conn.execute("SELECT * FROM calendar_event ORDER BY starts_at").fetchall()
    return [
        CalendarEvent.model_validate({**dict(row), "all_day": bool(row["all_day"])}) for row in rows
    ]


def get_athlete(conn: sqlite3.Connection) -> Athlete | None:
    """The single athlete profile, if one has been saved."""
    row = conn.execute("SELECT * FROM athlete WHERE id = 1").fetchone()
    return Athlete.model_validate(dict(row)) if row is not None else None


def save_athlete(conn: sqlite3.Connection, athlete: Athlete) -> None:
    """Create or replace the single athlete profile."""
    data = athlete.model_dump(exclude={"id"})
    columns = ", ".join(data)
    with conn:
        conn.execute(
            f"INSERT INTO athlete (id, {columns}) VALUES (1, {', '.join('?' for _ in data)}) "
            f"ON CONFLICT(id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in data)}",
            list(data.values()),
        )


def checkins(conn: sqlite3.Connection, since: date | None = None) -> list[Checkin]:
    """Check-ins from a date onwards, oldest first."""
    rows = conn.execute(
        "SELECT * FROM checkin WHERE date >= ? ORDER BY date", ((since or date.min).isoformat(),)
    ).fetchall()
    return [Checkin.model_validate(dict(row)) for row in rows]


def save_checkin(conn: sqlite3.Connection, checkin: Checkin) -> None:
    """Store the day's check-in, replacing an earlier one for the same date."""
    with conn:
        conn.execute(
            """
            INSERT INTO checkin (date, feel, niggle, ill, resting_hr, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET feel = excluded.feel, niggle = excluded.niggle,
                ill = excluded.ill, resting_hr = excluded.resting_hr, notes = excluded.notes
            """,
            (
                checkin.date.isoformat(),
                checkin.feel,
                checkin.niggle,
                int(checkin.ill),
                checkin.resting_hr,
                checkin.notes,
            ),
        )


def applied_rules(conn: sqlite3.Connection) -> set[tuple[int | None, str]]:
    """(session_id, rule) pairs already in the adjustment log, for idempotent rules."""
    rows = conn.execute("SELECT DISTINCT session_id, rule FROM adjustment").fetchall()
    return {(row["session_id"], row["rule"]) for row in rows}


def _insert_planned_session(
    conn: sqlite3.Connection,
    plan_id: int,
    session: PlannedSession,
) -> int:
    cursor = conn.execute(
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
            session.start_time,
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
    if cursor.lastrowid is None:
        raise RuntimeError("Failed to insert planned session")
    return int(cursor.lastrowid)


def replace_continuous_sessions(
    conn: sqlite3.Connection,
    plan_id: int,
    sessions: list[PlannedSession],
    *,
    today: date,
    replanned_at: str,
) -> int:
    """Replace upcoming planned sessions atomically while retaining completed work."""
    try:
        timestamp = datetime.fromisoformat(replanned_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("replanned_at must be an ISO 8601 UTC timestamp") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
        raise ValueError("replanned_at must be an ISO 8601 UTC timestamp")
    if any(session.date < today for session in sessions):
        raise ValueError("replanned sessions must not be dated before today")

    with conn:
        conn.execute("BEGIN IMMEDIATE")
        plan = conn.execute(
            "SELECT mode, status FROM plan WHERE id = ?",
            (plan_id,),
        ).fetchone()
        if plan is None or plan["mode"] != "continuous" or plan["status"] != "active":
            raise ValueError(f"Plan {plan_id} is not an active continuous plan")
        before_rows = conn.execute(
            """
            SELECT id, date, sport, kind, title
            FROM session
            WHERE plan_id = ? AND date >= ? AND status IN ('planned', 'moved')
            ORDER BY date, id
            """,
            (plan_id, today.isoformat()),
        ).fetchall()
        before = [dict(row) for row in before_rows]
        done_rows = conn.execute(
            """
            SELECT id, date, sport, kind, title
            FROM session
            WHERE plan_id = ? AND date >= ? AND status = 'done'
            ORDER BY date, id
            """,
            (plan_id, today.isoformat()),
        ).fetchall()
        completed = {(row["date"], row["sport"], row["kind"]) for row in done_rows}
        new_sessions = [
            session
            for session in sessions
            if (session.date.isoformat(), session.sport, session.kind) not in completed
        ]
        conn.execute(
            """
            DELETE FROM session
            WHERE plan_id = ? AND date >= ? AND status IN ('planned', 'moved')
            """,
            (plan_id, today.isoformat()),
        )
        inserted_ids = [_insert_planned_session(conn, plan_id, session) for session in new_sessions]
        after = [
            {
                "id": row["id"],
                "date": row["date"],
                "sport": row["sport"],
                "kind": row["kind"],
                "title": row["title"],
                "status": "done",
            }
            for row in done_rows
        ] + [
            {
                "date": session.date.isoformat(),
                "sport": session.sport,
                "kind": session.kind,
                "title": session.title,
                "status": "planned",
            }
            for session in new_sessions
        ]
        conn.execute(
            """
            INSERT INTO adjustment (created_at, session_id, rule, before_json, after_json, reason)
            VALUES (?, NULL, 'weekly_replan', ?, ?, ?)
            """,
            (
                timestamp.astimezone(timezone.utc).isoformat(),
                json.dumps(before, separators=(",", ":")),
                json.dumps(after, separators=(",", ":")),
                "Regenerated the upcoming continuous plan; completed sessions were retained.",
            ),
        )
        conn.execute(
            """
            INSERT INTO kv (key, value) VALUES ('last_replanned_at', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (timestamp.astimezone(timezone.utc).isoformat(),),
        )
    return len(inserted_ids)


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
        # One active plan at a time; the new plan supersedes any earlier one.
        conn.execute("UPDATE plan SET status = 'complete' WHERE status = 'active'")
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
            _insert_planned_session(conn, plan_id, session)
    return plan_id


__all__ = [
    "connect",
    "default_db_path",
    "backup",
    "migrate",
    "schema_version",
    "save_plan",
    "insert_activity",
    "from_row",
    "to_row",
    "apply_changes",
    "save_commitment",
    "list_commitments",
    "delete_commitment",
    "cache_calendar_events",
    "get_kv",
    "set_kv",
    "delete_kv",
    "kv_keys",
    "activities",
    "link_activity",
    "find_activity",
    "calendar_events",
    "get_athlete",
    "save_athlete",
    "checkins",
    "save_checkin",
    "applied_rules",
    "active_plan",
    "plan_sessions",
    "get_session",
    "session_adjustments",
    "replace_continuous_sessions",
]


def garmin_pushes(conn: sqlite3.Connection) -> dict[int, GarminPush]:
    """Every pushed workout, by session id."""
    rows = conn.execute("SELECT * FROM garmin_push").fetchall()
    return {row["session_id"]: GarminPush.model_validate(dict(row)) for row in rows}


def save_garmin_push(conn: sqlite3.Connection, push: GarminPush) -> None:
    """Record (or replace) a session's pushed workout; committed at once (per-call persistence)."""
    values = push.model_dump(mode="json")
    with conn:
        conn.execute(
            f"INSERT OR REPLACE INTO garmin_push ({', '.join(values)}) "
            f"VALUES ({', '.join('?' * len(values))})",
            tuple(values.values()),
        )


def delete_garmin_push(conn: sqlite3.Connection, session_id: int) -> None:
    """Forget a session's pushed workout."""
    with conn:
        conn.execute("DELETE FROM garmin_push WHERE session_id = ?", (session_id,))


def activity_starting_at(conn: sqlite3.Connection, started_at: str) -> int | None:
    """An activity already stored with this start time (the same run from another source)."""
    row = conn.execute("SELECT id FROM activity WHERE started_at = ?", (started_at,)).fetchone()
    return int(row["id"]) if row is not None else None


def session_activity(conn: sqlite3.Connection, session_id: int) -> Activity | None:
    """The activity linked to a session, if any."""
    row = conn.execute(
        "SELECT * FROM activity WHERE session_id = ? ORDER BY id LIMIT 1", (session_id,)
    ).fetchone()
    return from_row(row) if row is not None else None
