from __future__ import annotations

import argparse
import getpass
import logging
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import get_args

from runcoach import db
from runcoach.config import load_config, load_scheduling_config
from runcoach.display import main_pace, monday, structure
from runcoach.domain.models import (
    DISTANCE_METRES,
    Commitment,
    Distance,
    PlanInput,
    PlannedSession,
    commitment_problem,
)
from runcoach.engine.plan import InfeasibleGoal, generate_race_plan_result, plan_snapshot
from runcoach.ingest.inbox import ingest_file
from runcoach.integrations import garmin
from runcoach.sync import run_sync


def _save_commitment(build: Callable[[], Commitment], label: str) -> int:
    """Validate and store a hand-entered commitment or event."""
    try:
        commitment = build()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    problem = commitment_problem(commitment)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    conn = db.connect()
    try:
        print(f"Saved {label} with id={db.save_commitment(conn, commitment)}")
        return 0
    finally:
        conn.close()


def _commitment_add(args: argparse.Namespace) -> int:
    return _save_commitment(
        lambda: Commitment(
            kind=args.kind,
            sport=args.sport,
            title=args.title,
            weekday=args.weekday,
            start_date=args.start_date,
            end_date=args.end_date,
            start_time=args.start_time,
            duration_min=args.duration,
            rpe=args.rpe,
            severity=args.severity,
        ),
        "commitment",
    )


def _event_add(args: argparse.Namespace) -> int:
    start, end = (args.start, args.end) if args.kind == "block" else (args.date, args.date)
    return _save_commitment(
        lambda: Commitment(
            kind=args.kind,
            sport=args.sport,
            title=args.title or args.kind.title(),
            weekday=args.weekday,
            start_date=start,
            end_date=end,
            start_time=args.start_time,
            duration_min=args.duration,
            rpe=args.rpe,
            severity=args.severity,
            minimal_plan=args.minimal_plan,
        ),
        "event",
    )


def _commitment_list(_args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        for commitment in db.list_commitments(conn):
            print(commitment.model_dump_json())
        return 0
    finally:
        conn.close()


def _commitment_remove(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        db.delete_commitment(conn, int(args.id))
        print(f"Deleted {args.id}")
        return 0
    finally:
        conn.close()


def _print_weeks(sessions: list[PlannedSession]) -> None:
    week_start: date | None = None
    for session in sorted(sessions, key=lambda s: (s.date, s.start_time or "")):
        week_monday = monday(session.date)
        if week_monday != week_start:
            week_start = week_monday
            week = [s for s in sessions if monday(s.date) == week_monday]
            km = sum(s.distance_m or 0 for s in week if s.sport == "run") / 1000
            load = sum(s.leg_load for s in week)
            print(f"\nWeek of {week_monday.isoformat()}  run {km:.1f} km  leg load {load:.0f}")
        km_text = f"{(session.distance_m or 0) / 1000:5.1f} km" if session.distance_m else " " * 8
        print(
            f"  {session.date.strftime('%a %d %b')} {session.start_time or '':5}  "
            f"{(session.title + ' ' + structure(session.steps)).strip():<30} "
            f"{session.duration_min or 0:4d} min {km_text}  "
            f"{main_pace(session.steps)}"
        )


def _plan_generate(args: argparse.Namespace) -> int:
    inp = PlanInput(
        distance=args.distance,
        goal_date=date.fromisoformat(args.date),
        vdot=float(args.vdot),
        runs_per_week=int(args.runs),
        climbing_target_per_week=int(args.climbs),
        current_weekly_km=float(args.current_km),
        race_name=args.distance,
    )
    cfg = load_config()
    window = load_scheduling_config(cfg).progression.spike_window_days
    conn = db.connect()
    try:
        commitments = db.list_commitments(conn)
        longest = db.longest_run_km_since(conn, datetime.now(timezone.utc) - timedelta(days=window))
    finally:
        conn.close()
    inp = inp.model_copy(update={"longest_recent_run_km": longest})
    try:
        result = generate_race_plan_result(
            inp, today=date.today(), cfg=cfg, commitments=commitments
        )
    except InfeasibleGoal as exc:
        print(str(exc))
        return 1
    for warning in result.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    for item in result.dropped:
        print(f"Dropped {item.session.title}: {item.reason}", file=sys.stderr)
    for violation in result.violations:
        print(f"Safety violation ({violation.kind}): {violation.message}", file=sys.stderr)
    _print_weeks(result.sessions)

    if not args.save:
        return 0
    if result.violations:
        print("Not saved: the plan fails the safety checks above.", file=sys.stderr)
        return 1
    conn = db.connect()
    try:
        plan_id = db.save_plan(
            conn,
            result.sessions,
            mode="race",
            goal_distance_m=DISTANCE_METRES[inp.distance],
            goal_date=inp.goal_date,
            config_snapshot=plan_snapshot(inp, commitments),
        )
        print(f"Saved plan to database with id={plan_id}")
    finally:
        conn.close()
    return 0


def _ingest(args: argparse.Namespace) -> int:
    path = Path(args.file)
    conn = db.connect()
    try:
        result = ingest_file(
            conn, path.read_bytes(), path.name, load_scheduling_config(load_config())
        )
    except (OSError, ValueError) as exc:
        print(f"Could not ingest {path.name}: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    state = "stored" if result.added else "already stored"
    print(f"{path.name}: {state} as activity {result.activity_id}")
    return 0


def _sync(_args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx logs each request URL, and the calendar's is a secret.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    conn = db.connect()
    try:
        report = run_sync(conn, load_config(), datetime.now(timezone.utc))
    finally:
        conn.close()
    for step, outcome in report.items():
        print(f"{step:<11} {outcome}")
    return 1 if any(outcome.startswith("error") for outcome in report.values()) else 0


def _garmin_login(_args: argparse.Namespace) -> int:
    cfg = load_scheduling_config(load_config()).garmin
    print("Log in to Garmin Connect. Only the session token is saved, in", cfg.token_dir)
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Password (not stored): ")
    try:
        garmin.login(cfg, email, password, lambda: input("MFA code: ").strip())
    except Exception as exc:  # one attempt only, so Garmin doesn't lock the account
        print(f"Login failed ({type(exc).__name__}). Check the details and try again later.")
        return 1
    print('Logged in. Set [garmin] enabled = true (and [export] backend = "garmin" to push).')
    return 0


def _garmin_status(_args: argparse.Namespace) -> int:
    try:
        garmin.connect(load_scheduling_config(load_config()).garmin)
    except RuntimeError as exc:
        print(exc)
        return 1
    print("Garmin Connect: logged in.")
    return 0


def _backup(_args: argparse.Namespace) -> int:
    storage = load_config().storage
    directory = db.ROOT / str(storage.backup_dir)  # an absolute backup_dir replaces ROOT
    conn = db.connect()
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        print(f"Backed up to {db.backup(conn, directory, int(storage.backup_keep), stamp)}")
    finally:
        conn.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="RunCoach CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    commitment_parser = subparsers.add_parser("commitment")
    commitment_sub = commitment_parser.add_subparsers(dest="action", required=True)

    commitment_add = commitment_sub.add_parser("add")
    commitment_add.add_argument("--kind", required=True)
    commitment_add.add_argument("--title", required=True)
    commitment_add.add_argument("--sport")
    commitment_add.add_argument("--weekday", type=int)
    commitment_add.add_argument("--start-date")
    commitment_add.add_argument("--end-date")
    commitment_add.add_argument("--start-time")
    commitment_add.add_argument("--duration", type=int)
    commitment_add.add_argument("--rpe", type=float)
    commitment_add.add_argument("--severity", type=int)
    commitment_add.set_defaults(func=_commitment_add)

    commitment_list = commitment_sub.add_parser("list")
    commitment_list.set_defaults(func=_commitment_list)

    commitment_remove = commitment_sub.add_parser("remove")
    commitment_remove.add_argument("--id", required=True)
    commitment_remove.set_defaults(func=_commitment_remove)

    event_parser = subparsers.add_parser("event")
    event_sub = event_parser.add_subparsers(dest="action", required=True)

    event_add = event_sub.add_parser("add")
    event_add.add_argument("--kind", choices=("fixed", "penalty", "block", "credit"), required=True)
    event_add.add_argument("--title")
    event_add.add_argument("--sport")
    event_add.add_argument("--weekday", type=int)
    event_add.add_argument("--date")
    event_add.add_argument("--start")
    event_add.add_argument("--end")
    event_add.add_argument("--start-time")
    event_add.add_argument("--duration", type=int)
    event_add.add_argument("--rpe", type=float)
    event_add.add_argument("--severity", type=int, choices=(1, 2, 3))
    event_add.add_argument("--minimal-plan", action="store_true")
    event_add.set_defaults(func=_event_add)

    event_list = event_sub.add_parser("list")
    event_list.set_defaults(func=_commitment_list)

    event_remove = event_sub.add_parser("remove")
    event_remove.add_argument("--id", required=True)
    event_remove.set_defaults(func=_commitment_remove)

    generate_parser = subparsers.add_parser("plan")
    generate_sub = generate_parser.add_subparsers(dest="action", required=True)
    plan_generate = generate_sub.add_parser("generate")
    plan_generate.add_argument("--distance", required=True, choices=get_args(Distance))
    plan_generate.add_argument("--date", required=True)
    plan_generate.add_argument("--vdot", required=True)
    plan_generate.add_argument("--runs", required=True)
    plan_generate.add_argument("--climbs", default="2")
    plan_generate.add_argument("--current-km", required=True)
    plan_generate.add_argument(
        "--save",
        action="store_true",
        help="Persist the generated plan to SQLite",
    )
    plan_generate.set_defaults(func=_plan_generate)
    ingest_parser = subparsers.add_parser("ingest", help="Store a GPX or FIT activity file")
    ingest_parser.add_argument("file")
    ingest_parser.set_defaults(func=_ingest)
    sync_parser = subparsers.add_parser("sync", help="Inbox, calendar, weather, matching, rules")
    sync_parser.set_defaults(func=_sync)
    garmin_parser = subparsers.add_parser("garmin", help="Garmin Connect login")
    garmin_sub = garmin_parser.add_subparsers(dest="action", required=True)
    garmin_sub.add_parser("login", help="Log in once; saves a session token").set_defaults(
        func=_garmin_login
    )
    garmin_sub.add_parser("status", help="Check the saved session still works").set_defaults(
        func=_garmin_status
    )
    backup_parser = subparsers.add_parser("backup", help="Copy the database to [storage]")
    backup_parser.set_defaults(func=_backup)
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
