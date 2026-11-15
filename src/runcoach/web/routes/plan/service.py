from __future__ import annotations

from datetime import date

from fastapi import HTTPException

from runcoach.config import load_config
from runcoach.domain.models import PlanInput
from runcoach.engine.plan import InfeasibleGoal, generate_race_plan_result
from runcoach.web.routes.plan.model import (
    DroppedPlanSession,
    PlanCreateRequest,
    PlanResponse,
)


class PlanService:
    @staticmethod
    def generate(request: PlanCreateRequest) -> PlanResponse:
        """Preview a race plan with its warnings and safety violations; nothing is saved."""
        try:
            result = generate_race_plan_result(
                PlanInput(**request.model_dump()),
                today=date.today(),
                cfg=load_config(),
            )
        except InfeasibleGoal as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PlanResponse(
            sessions=result.sessions,
            dropped=[
                DroppedPlanSession(session=item.session, reason=item.reason)
                for item in result.dropped
            ],
            warnings=result.warnings,
            violations=[f"{v.kind}: {v.message}" for v in result.violations],
        )
