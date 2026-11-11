from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, datetime

from runcoach import db
from runcoach.config import ConfigNode
from runcoach.domain.models import Athlete, Commitment
from runcoach.engine.continuous import (
    ContinuousPlanResult,
    History,
    generate_continuous_plan_result,
)
from runcoach.engine.replan import should_replan


def replan_continuous_if_due(
    conn: sqlite3.Connection,
    plan_id: int,
    athlete: Athlete,
    history: History,
    today: date,
    now_utc: datetime,
    events: list[Commitment],
    cfg: ConfigNode,
) -> ContinuousPlanResult | None:
    last_replanned_at = db.get_kv(conn, "last_replanned_at")
    if not should_replan(today, now_utc, last_replanned_at, cfg):
        return None

    plan = conn.execute(
        "SELECT start_date, mode, status FROM plan WHERE id = ?",
        (plan_id,),
    ).fetchone()
    if plan is None or plan["mode"] != "continuous" or plan["status"] != "active":
        raise ValueError(f"Plan {plan_id} is not an active continuous plan")
    current_history = history
    if current_history.plan_start_date is None:
        current_history = replace(
            current_history,
            plan_start_date=date.fromisoformat(plan["start_date"]),
        )
    result = generate_continuous_plan_result(athlete, current_history, today, events, cfg)
    db.replace_continuous_sessions(
        conn,
        plan_id,
        result.sessions,
        today=today,
        replanned_at=now_utc.isoformat(),
    )
    return result
