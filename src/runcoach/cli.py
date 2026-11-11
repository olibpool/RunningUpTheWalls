from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from runcoach import db
from runcoach.config import load_config
from runcoach.domain.models import Commitment, PlanInput
from runcoach.engine.plan import InfeasibleGoal, generate_race_plan_result


def _commitment_add(args: argparse.Namespace) -> int:
    commitment = Commitment(
        kind=args.kind,
        sport=args.sport,
        title=args.title,
        weekday=int(args.weekday) if args.weekday is not None else None,
        start_date=args.start_date,
        end_date=args.end_date,
        start_time=args.start_time,
        duration_min=int(args.duration) if args.duration is not None else None,
        rpe=float(args.rpe) if args.rpe is not None else None,
        severity=int(args.severity) if args.severity is not None else None,
        source="manual",
        active=True,
    )
    conn = db.connect()
    try:
        saved_id = db.save_commitment(conn, commitment)
        print(f"Saved commitment with id={saved_id}")
        return 0
    finally:
        conn.close()


def _commitment_list(_args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        for row in db.list_commitments(conn):
            print(json.dumps(row, sort_keys=True))
        return 0
    finally:
        conn.close()


def _commitment_remove(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        db.delete_commitment(conn, int(args.id))
        print(f"Deleted commitment {args.id}")
        return 0
    finally:
        conn.close()


def _event_add(args: argparse.Namespace) -> int:
    if args.weekday is not None and not 0 <= args.weekday <= 6:
        print("--weekday must be in the range 0-6, where 0 is Monday.", file=sys.stderr)
        return 2
    if args.kind == "block":
        if not args.start or not args.end:
            print("A block requires --start and --end dates.", file=sys.stderr)
            return 2
        start_date, end_date = args.start, args.end
    elif args.kind == "fixed":
        if args.weekday is None and not args.date:
            print("A fixed event requires --weekday or --date.", file=sys.stderr)
            return 2
        if not args.sport or not args.start_time or not args.duration:
            print(
                "A fixed event requires --sport, --start-time, and --duration.",
                file=sys.stderr,
            )
            return 2
        start_date = args.date
        end_date = args.date
    else:
        if not args.date:
            print(f"A {args.kind} event requires --date.", file=sys.stderr)
            return 2
        start_date, end_date = args.date, args.date

    if args.kind == "penalty" and args.severity is None:
        print("A penalty event requires --severity (1-3).", file=sys.stderr)
        return 2
    if args.kind == "credit" and (args.rpe is None or args.duration is None):
        print("A credit event requires --rpe and --duration.", file=sys.stderr)
        return 2
    if args.minimal_plan and args.kind != "block":
        print("--minimal-plan is only valid for a block event.", file=sys.stderr)
        return 2
    try:
        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else None
        if start and end and start > end:
            print("The event end date must not precede its start date.", file=sys.stderr)
            return 2
    except ValueError:
        print("Event dates must use YYYY-MM-DD format.", file=sys.stderr)
        return 2

    event = Commitment(
        kind=args.kind,
        sport=args.sport,
        title=args.title or args.kind.replace("_", " ").title(),
        weekday=args.weekday,
        start_date=start_date,
        end_date=end_date,
        start_time=args.start_time,
        duration_min=args.duration,
        rpe=args.rpe,
        severity=args.severity,
        source="manual",
        active=True,
        minimal_plan=args.minimal_plan,
    )
    conn = db.connect()
    try:
        saved_id = db.save_commitment(conn, event)
        print(f"Saved event with id={saved_id}")
        return 0
    finally:
        conn.close()


def _event_list(_args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        for row in db.list_commitments(conn):
            print(json.dumps(row, sort_keys=True))
        return 0
    finally:
        conn.close()


def _event_remove(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        db.delete_commitment(conn, int(args.id))
        print(f"Deleted event {args.id}")
        return 0
    finally:
        conn.close()


def _plan_generate(args: argparse.Namespace) -> int:
    try:
        result = generate_race_plan_result(
            PlanInput(
                distance_km=float(args.distance),
                goal_date=date.fromisoformat(args.date),
                vdot=float(args.vdot),
                runs_per_week=int(args.runs),
                climbing_target_per_week=int(args.climbs),
                current_weekly_km=float(args.current_km),
                race_name=args.distance,
            ),
            today=date.today(),
            cfg=load_config(),
        )
        for warning in result.warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        for item in result.dropped:
            print(f"Dropped {item.session.title}: {item.reason}", file=sys.stderr)
        plan = result.sessions
        print(json.dumps([session.model_dump(mode="json") for session in plan], indent=2))

        if args.save:
            conn = db.connect()
            try:
                plan_id = db.save_plan(
                    conn,
                    plan,
                    mode="race",
                    goal_distance_m=int(round(float(args.distance) * 1000)),
                    goal_date=date.fromisoformat(args.date),
                    config_snapshot={
                        "distance_km": float(args.distance),
                        "goal_date": args.date,
                        "vdot": float(args.vdot),
                        "runs_per_week": int(args.runs),
                        "climbing_target_per_week": int(args.climbs),
                        "current_weekly_km": float(args.current_km),
                    },
                )
                print(f"Saved plan to database with id={plan_id}")
            finally:
                conn.close()
        return 0
    except InfeasibleGoal as exc:
        print(str(exc))
        return 1


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
    event_list.set_defaults(func=_event_list)

    event_remove = event_sub.add_parser("remove")
    event_remove.add_argument("--id", required=True)
    event_remove.set_defaults(func=_event_remove)

    generate_parser = subparsers.add_parser("plan")
    generate_sub = generate_parser.add_subparsers(dest="action", required=True)
    plan_generate = generate_sub.add_parser("generate")
    plan_generate.add_argument("--distance", required=True)
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
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
