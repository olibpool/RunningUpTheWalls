from __future__ import annotations

from fastapi import APIRouter

from runcoach.web.routes import (
    activities,
    checkin,
    events,
    export,
    health,
    home,
    plans,
    sessions,
    settings,
    week,
)

router = APIRouter()
for module in (health, home, week, sessions, plans, events, activities, checkin, settings, export):
    router.include_router(module.router)
