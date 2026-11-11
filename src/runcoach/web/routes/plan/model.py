from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from runcoach.domain.models import PlannedSession


class PlanCreateRequest(BaseModel):
    distance_km: float = Field(gt=0.0)
    goal_date: date
    vdot: float = Field(gt=0.0)
    runs_per_week: int = Field(ge=2, le=6)
    climbing_target_per_week: int = Field(default=2, ge=0, le=3)
    current_weekly_km: float = Field(ge=0.0)
    race_name: str = "race"


class DroppedPlanSession(BaseModel):
    session: PlannedSession
    reason: str


class PlanResponse(BaseModel):
    sessions: list[PlannedSession] = Field(default_factory=list)
    dropped: list[DroppedPlanSession] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
