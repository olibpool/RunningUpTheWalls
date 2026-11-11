from __future__ import annotations

from fastapi import APIRouter

from runcoach.web.routes.health.model import HealthStatus
from runcoach.web.routes.health.service import HealthService

router = APIRouter()


@router.get("/healthz")
async def healthz() -> HealthStatus:
    return HealthService.get_status()


@router.get("/")
async def root() -> dict[str, str]:
    return {"message": "hello"}
