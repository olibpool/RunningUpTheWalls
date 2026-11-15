from __future__ import annotations

from fastapi import APIRouter

from runcoach.web.routes import (
    activities,
    checkin,
    events,
    health,
    plans,
    sessions,
    settings,
    week,
)

router = APIRouter()
for module in (health, week, sessions, plans, events, activities, checkin, settings):
    router.include_router(module.router)
