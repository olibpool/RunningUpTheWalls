from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, model_validator

from runcoach import db
from runcoach.config import ConfigNode, SchedulingConfig, load_scheduling_config
from runcoach.display import monday, phase
from runcoach.domain.models import DISTANCE_METRES, Distance, Plan, PlanInput, PlannedSession
from runcoach.engine.continuous import ContinuousPlanResult, generate_continuous_plan_result
from runcoach.engine.plan import (
    InfeasibleGoal,
    PlanGenerationResult,
    fixed_instances,
    generate_race_plan_result,
    plan_snapshot,
)
from runcoach.engine.scheduler import FixedInstance, fixed_leg_load
from runcoach.engine.vdot import vdot_from_race
from runcoach.sync import history_from_activities
from runcoach.web.deps import get_cfg, get_conn, parse_form, redirect, render, today
from runcoach.web.stats import stack_bars

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
    by_sport: dict[str, float]  # leg load, fixed commitments included
    phase: str | None


def by_week(
    sessions: Sequence[PlannedSession], fixed: list[FixedInstance] | None = None,
    cfg: SchedulingConfig | None = None,
) -> list[WeekSummary]:  # fmt: skip
    """Group sessions into Monday-start weeks with their run km and leg load by sport; fixed
    commitments (with `cfg` to cost them) count towards each week's load too."""
    weeks: dict[date, list[PlannedSession]] = {}
    for session in sessions:
        weeks.setdefault(monday(session.date), []).append(session)
    result = []
    for start, items in sorted(weeks.items()):
        sports: dict[str, float] = {}
        for s in items:
            sports[s.sport] = sports.get(s.sport, 0.0) + s.leg_load
        for f in fixed or []:
            if cfg is not None and monday(f.date) == start:
                sports[f.sport] = sports.get(f.sport, 0.0) + fixed_leg_load(f, cfg)
        result.append(
            WeekSummary(
                start=start,
                sessions=items,
                run_km=sum(s.distance_m or 0 for s in items if s.sport == "run") / 1000,
                leg_load=sum(s.leg_load for s in items),
                by_sport=sports,
                phase=next((p for s in items if (p := phase(s.reason))), None),
            )
        )
    return result


def _generate(
    conn: sqlite3.Connection, cfg: ConfigNode, inp: PlanInput
) -> tuple[PlanGenerationResult | None, str | None]:
    window = load_scheduling_config(cfg).progression.spike_window_days
    since = datetime.now(timezone.utc) - timedelta(days=window)
    inp = inp.model_copy(update={"longest_recent_run_km": db.longest_run_km_since(conn, since)})
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
            "active": "week",
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
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> HTMLResponse:
    """The whole plan: phases, load by sport week by week, and the goal."""
    plan = db.active_plan(conn)
    sessions = db.active_sessions(conn)
    typed = load_scheduling_config(cfg)
    fixed = (
        fixed_instances(db.list_commitments(conn), sessions[0].date, sessions[-1].date)[0]
        if sessions
        else []
    )
    weeks = by_week(sessions, fixed, typed)
    return render(
        request,
        "plan.html",
        {
            "active": "week",
            "plan": plan,
            "weeks": weeks,
            "bars": stack_bars(
                [(w.start, w.by_sport) for w in weeks],
                monday(today()),
                link=True,
                notes={
                    w.start: w.phase
                    for i, w in enumerate(weeks)
                    if w.phase and (i == 0 or weeks[i - 1].phase != w.phase)
                },
            ),  # fmt: skip
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new(request: Request, conn: Annotated[sqlite3.Connection, Depends(get_conn)]) -> HTMLResponse:
    """The plan wizard, with weekly distance taken from the last 4 weeks of recorded runs."""
    four_weeks_km = db.run_km_since(conn, datetime.now(timezone.utc) - timedelta(weeks=4))
    form: dict[str, Any] = {"distance": "half", "runs_per_week": 4, "climbing_target_per_week": 2}
    if four_weeks_km:
        form["current_weekly_km"] = round(four_weeks_km / 4, 1)
    return _wizard(request, form)


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


def _continuous(
    conn: sqlite3.Connection, cfg: ConfigNode
) -> tuple[ContinuousPlanResult | None, list[str]]:
    """Generate a continuous plan from the athlete profile and recent activities."""
    athlete = db.get_athlete(conn)
    if athlete is None:
        return None, ["Save your athlete profile in Settings first."]
    try:
        history = history_from_activities(conn, load_scheduling_config(cfg), today())
        result = generate_continuous_plan_result(
            athlete, history, today(), db.list_commitments(conn), cfg
        )
    except (RuntimeError, ValueError) as exc:
        return None, [f"{exc}. Connect Garmin in Settings, or upload a few recent runs."]
    return result, []


def _continuous_page(
    request: Request,
    result: ContinuousPlanResult | None,
    errors: list[str],
    current: Plan | None,
    status_code: int = 200,
) -> HTMLResponse:
    return render(
        request,
        "plan_continuous.html",
        {
            "active": "week",
            "result": result,
            "errors": errors,
            "weeks": by_week(result.sessions) if result else [],
            "current": current,
        },
        status_code=status_code,
    )


@router.get("/continuous", response_class=HTMLResponse)
def continuous_preview(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> HTMLResponse:
    """Preview the next weeks of a goal-free plan built from recent load."""
    result, errors = _continuous(conn, cfg)
    return _continuous_page(request, result, errors, db.active_plan(conn))


@router.post("/continuous/save", response_model=None)
def continuous_save(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    cfg: Annotated[ConfigNode, Depends(get_cfg)],
) -> Response:
    """Store the continuous plan; sync re-plans it weekly from then on."""
    result, errors = _continuous(conn, cfg)
    if result is None or result.violations or not result.sessions:
        reasons = errors or ["Not saved: the plan fails the safety checks below."]
        return _continuous_page(request, result, reasons, db.active_plan(conn), status_code=400)
    commitments = db.list_commitments(conn)
    db.save_plan(
        conn,
        result.sessions,
        mode="continuous",
        config_snapshot={
            "mode": "continuous",
            "event_ids": [c.id for c in commitments if c.kind != "fixed" and c.id is not None],
        },
    )
    # Generated just now, so the next sync shouldn't immediately re-plan it.
    db.set_kv(conn, "last_replanned_at", datetime.now(timezone.utc).isoformat())
    return redirect(request, f"/week?start={monday(result.sessions[0].date).isoformat()}")
