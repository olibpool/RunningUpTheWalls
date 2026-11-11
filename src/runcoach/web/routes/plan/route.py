from __future__ import annotations

from fastapi import APIRouter

from runcoach.web.routes.plan.model import PlanCreateRequest, PlanResponse
from runcoach.web.routes.plan.service import PlanService

router = APIRouter(prefix="/plans", tags=["plans"])


@router.post("/generate", response_model=PlanResponse)
async def generate_plan(payload: PlanCreateRequest) -> PlanResponse:
    return PlanService.generate(payload)
