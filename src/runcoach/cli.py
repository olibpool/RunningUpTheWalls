from __future__ import annotations

import argparse
import json
from datetime import date

from runcoach import db
from runcoach.domain.models import Commitment, PlanInput
from runcoach.engine.plan import InfeasibleGoal, generate_race_plan


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


def _plan_generate(args: argparse.Namespace) -> int:
    try:
        plan = generate_race_plan(
            PlanInput(
                distance_km=float(args.distance),
                goal_date=date.fromisoformat(args.date),
                vdot=float(args.vdot),
                runs_per_week=int(args.runs),
                current_weekly_km=float(args.current_km),
                race_name=args.distance,
            ),
            today=date.today(),
            cfg={},
        )
        print(json.dumps([session.model_dump() for session in plan], indent=2))

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

    generate_parser = subparsers.add_parser("plan")
    generate_sub = generate_parser.add_subparsers(dest="action", required=True)
    plan_generate = generate_sub.add_parser("generate")
    plan_generate.add_argument("--distance", required=True)
    plan_generate.add_argument("--date", required=True)
    plan_generate.add_argument("--vdot", required=True)
    plan_generate.add_argument("--runs", required=True)
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
