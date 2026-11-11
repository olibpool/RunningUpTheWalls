from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Sport = Literal["run", "climb", "football", "rest", "other"]


class Step(BaseModel):
    kind: Literal["warmup", "work", "recover", "cooldown", "steady", "repeat"]
    duration_s: int | None = None
    distance_m: int | None = None
    pace_min_s_per_km: float | None = None
    pace_max_s_per_km: float | None = None
    rpe: float | None = None
    repeats: int | None = None
    children: list["Step"] = Field(default_factory=list)


class PlannedSession(BaseModel):
    date: date
    sport: Sport
    kind: str
    title: str
    steps: list[Step] = Field(default_factory=list)
    duration_min: int | None = None
    distance_m: int | None = None
    rpe: float | None = None
    leg_load: float = 0.0
    finger_load: float = 0.0
    reason: str = ""


class Athlete(BaseModel):
    id: int = 1
    name: str | None = None
    vdot: float
    vdot_source: str = "manual"
    vdot_updated_at: str
    runs_per_week: int
    climbing_target_per_week: int = 2
    max_weekly_load: float | None = None
    timezone: str = "Europe/London"


class PlanInput(BaseModel):
    distance_km: float
    goal_date: date
    vdot: float
    runs_per_week: int = 4
    current_weekly_km: float = 0.0
    race_name: str = "race"


class Plan(BaseModel):
    id: int | None = None
    mode: str = "race"
    goal_distance_m: int | None = None
    goal_date: str | None = None
    start_date: str
    status: str = "active"
    created_at: str
    config_snapshot: str = "{}"


class Activity(BaseModel):
    id: int | None = None
    source: str
    external_id: str | None = None
    sport: str
    started_at: str
    duration_s: int
    distance_m: float | None = None
    avg_hr: int | None = None
    rpe: float | None = None
    load: float | None = None
    session_id: int | None = None
    raw_path: str | None = None


class Commitment(BaseModel):
    id: int | None = None
    kind: str
    sport: str | None = None
    title: str
    weekday: int | None = None
    start_date: str | None = None
    end_date: str | None = None
    start_time: str | None = None
    duration_min: int | None = None
    rpe: float | None = None
    severity: int | None = None
    source: str = "manual"
    external_id: str | None = None
    active: bool = True


class Adjustment(BaseModel):
    id: int | None = None
    created_at: str
    session_id: int | None = None
    rule: str
    before_json: str | None = None
    after_json: str | None = None
    reason: str


class Checkin(BaseModel):
    date: date
    feel: int | None = None
    niggle: str | None = None
    ill: bool = False
    resting_hr: int | None = None
    notes: str | None = None
