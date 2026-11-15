from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, model_validator

from runcoach import db
from runcoach.config import ConfigNode
from runcoach.display import monday
from runcoach.domain.models import DISTANCE_METRES, Distance, PlanInput, PlannedSession
from runcoach.engine.plan import (
    InfeasibleGoal,
    PlanGenerationResult,
    generate_race_plan_result,
    plan_snapshot,
)
from runcoach.engine.vdot import vdot_from_race
from runcoach.web.deps import get_cfg, get_conn, parse_form, redirect, render, today

router = APIRouter(prefix="/plan")


def race_seconds(text: str) -> int:
    """Parse mm:ss or h:mm:ss into seconds."""
    parts = [int(part) for part in text.split(":")]
    if not 2 <= len(parts) <= 3 or any(p < 0 for p in parts) or any(p > 59 for p in parts[1:]):
        raise ValueError("race time must look like mm:ss or h:mm:ss")
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    if seconds <= 0:
        raise ValueError("race time must be positive")
    return seconds


class PlanForm(BaseModel):
    distance: Distance
    goal_date: date
    runs_per_week: int = Field(default=4, ge=2, le=6)
    climbing_target_per_week: int = Field(default=2, ge=0, le=3)
    current_weekly_km: float = Field(ge=0)
    vdot: float | None = Field(default=None, ge=20, le=90)
    race_distance: Distance | None = None
    race_time: str | None = None

    @model_validator(mode="after")
    def need_fitness(self) -> PlanForm:
        if self.vdot is None and not (self.race_distance and self.race_time):
            raise ValueError("give a recent race distance and time, or a VDOT")
        if self.race_time:
            race_seconds(self.race_time)
        return self

    def plan_input(self) -> PlanInput:
        """The engine input; a race result takes precedence over a typed VDOT."""
        vdot = self.vdot
        if self.race_distance and self.race_time:
            vdot = vdot_from_race(DISTANCE_METRES[self.race_distance], race_seconds(self.race_time))
        assert vdot is not None  # need_fitness guarantees one source
        return PlanInput(
            distance=self.distance,
            goal_date=self.goal_date,
            vdot=round(vdot, 1),
            runs_per_week=self.runs_per_week,
            climbing_target_per_week=self.climbing_target_per_week,
            current_weekly_km=self.current_weekly_km,
        )


@dataclass(frozen=True)
class WeekSummary:
    start: date
    sessions: list[PlannedSession]
    run_km: float
    leg_load: float


def by_week(sessions: list[PlannedSession]) -> list[WeekSummary]:
    """Group sessions into Monday-start weeks with their run km and leg load."""
    weeks: dict[date, list[PlannedSession]] = {}
    for session in sessions:
        weeks.setdefault(monday(session.date), []).append(session)
    return [
        WeekSummary(
            start=start,
            sessions=items,
            run_km=sum(s.distance_m or 0 for s in items if s.sport == "run") / 1000,
            leg_load=sum(s.leg_load for s in items),
        )
        for start, items in sorted(weeks.items())
    ]


def _generate(
    conn: sqlite3.Connection, cfg: ConfigNode, inp: PlanInput
) -> tuple[PlanGenerationResult | None, str | None]:
    try:
        return generate_race_plan_result(
            inp, today(), cfg, commitments=db.list_commitments(conn)
        ), None
    except InfeasibleGoal as exc:
        return None, exc.message


def _wizard(
    request: Request,
    form: dict[str, Any],
    *,
    errors: list[str] | None = None,
    result: PlanGenerationResult | None = None,
    inp: PlanInput | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render(
        request,
        "plan_new.html",
        {
            "active": "plan",
            "form": form,
            "errors": errors or [],
            "result": result,
            "inp": inp,
            "weeks": by_week(result.sessions) if result else [],
        },
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
def overview(
    request: Request, conn: Annotated[sqlite3.Connection, Depends(get_conn)]
) -> HTMLResponse:
    """The active plan, week by week."""
    plan = db.active_plan(conn)
    sessions: list[PlannedSession] = (
        list(db.plan_sessions(conn, plan.id)) if plan and plan.id else []
    )
    return render(
        request, "plan.html", {"active": "plan", "plan": plan, "weeks": by_week(sessions)}
    )


@router.get("/new", response_class=HTMLResponse)
def new(request: Request) -> HTMLResponse:
    """The plan wizard with sensible starting values."""
    return _wizard(request, {"distance": "half", "runs_per_week": 4, "climbing_target_per_week": 2})


@router.post("/new", response_class=HTMLResponse)
async def preview(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> HTMLResponse:
    """Generate a preview with warnings, safety violations or the infeasible reason."""
    raw = await request.form()
    form, errors = parse_form(PlanForm, raw)
    if form is None:
        return _wizard(request, dict(raw), errors=errors, status_code=400)
    inp = form.plan_input()
    result, infeasible = _generate(conn, cfg, inp)
    return _wizard(
        request, dict(raw), errors=[infeasible] if infeasible else [], result=result, inp=inp
    )


@router.post("/save", response_model=None)
async def save(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> Response:
    """Regenerate and store the plan; refuse when it fails the safety checks (D4)."""
    raw = await request.form()
    form, errors = parse_form(PlanForm, raw)
    if form is None:
        return _wizard(request, dict(raw), errors=errors, status_code=400)
    inp = form.plan_input()
    result, infeasible = _generate(conn, cfg, inp)
    if result is None or result.violations or not result.sessions:
        reason = infeasible or "Not saved: the plan fails the safety checks below."
        return _wizard(request, dict(raw), errors=[reason], result=result, inp=inp, status_code=400)
    db.save_plan(
        conn,
        result.sessions,
        mode="race",
        goal_distance_m=DISTANCE_METRES[inp.distance],
        goal_date=inp.goal_date,
        config_snapshot=plan_snapshot(inp, db.list_commitments(conn)),
    )
    return redirect(request, f"/week?start={monday(result.sessions[0].date).isoformat()}")
