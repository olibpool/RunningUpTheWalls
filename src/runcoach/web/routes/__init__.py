from __future__ import annotations

from fastapi import APIRouter

from runcoach.web.routes import activities, events, health, plans, sessions, week

router = APIRouter()
for module in (health, week, sessions, plans, events, activities):
    router.include_router(module.router)
