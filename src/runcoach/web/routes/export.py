from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from icalendar import Calendar, Event

from runcoach import db
from runcoach.display import describe_steps, structure
from runcoach.web.deps import TIMEZONE, get_conn

router = APIRouter(prefix="/export")


@router.get("/plan.ics")
def plan_ics(conn: Annotated[sqlite3.Connection, Depends(get_conn)]) -> Response:
    """The active plan as an iCalendar feed with stable UIDs (reachable only on the tailnet)."""
    calendar = Calendar()
    calendar.add("prodid", "-//Coachal//runcoach//EN")
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", "Coachal")
    plan = db.active_plan(conn)
    stamp = datetime.now(timezone.utc).replace(microsecond=0)
    for s in db.plan_sessions(conn, plan.id) if plan and plan.id else []:
        if s.status in {"skipped", "dropped"}:
            continue
        event = Event()
        event.add("uid", f"session-{s.id}@runcoach")
        event.add("dtstamp", stamp)
        start = time.fromisoformat(s.start_time) if s.start_time else None
        if start is None:
            event.add("dtstart", s.date)  # an all-day entry when no time is set
        else:
            event.add("dtstart", datetime.combine(s.date, start, tzinfo=TIMEZONE))
            event.add("duration", timedelta(minutes=s.duration_min or 0))
        event.add("summary", f"{s.title} {structure(s.steps)}".strip())
        event.add("description", "\n".join([*describe_steps(s.steps), "", s.reason]).strip())
        calendar.add_component(event)
    calendar.add_missing_timezones()
    return Response(calendar.to_ical(), media_type="text/calendar; charset=utf-8")
