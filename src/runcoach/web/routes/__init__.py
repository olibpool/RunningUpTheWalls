from __future__ import annotations

from fastapi import APIRouter

from runcoach.web.routes.activity.route import router as activity_router
from runcoach.web.routes.health.route import router as health_router
from runcoach.web.routes.plan.route import router as plan_router

router = APIRouter()
router.include_router(health_router)
router.include_router(plan_router)
router.include_router(activity_router)
