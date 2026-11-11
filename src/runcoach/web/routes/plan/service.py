from __future__ import annotations

from datetime import date

from fastapi import HTTPException

from runcoach.config import load_config
from runcoach.domain.models import PlanInput
from runcoach.engine.plan import InfeasibleGoal, generate_race_plan
from runcoach.web.routes.plan.model import PlanCreateRequest, PlanResponse


class PlanService:
    @staticmethod
    def generate(request: PlanCreateRequest) -> PlanResponse:
        try:
            sessions = generate_race_plan(
                PlanInput(
                    distance_km=request.distance_km,
                    goal_date=request.goal_date,
                    vdot=request.vdot,
                    runs_per_week=request.runs_per_week,
                    current_weekly_km=request.current_weekly_km,
                    race_name=request.race_name,
                ),
                today=date.today(),
                cfg=load_config(),
            )
        except InfeasibleGoal as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PlanResponse(sessions=sessions)
