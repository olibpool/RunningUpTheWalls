from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LOCAL_TIMEZONE = "Europe/London"  # planned sessions are local dates (working rule 8)

Sport = Literal["run", "climb", "football", "rest", "other"]
EventKind = Literal["fixed", "penalty", "block", "credit"]
Distance = Literal["5k", "10k", "half", "marathon"]
DISTANCE_METRES: dict[Distance, int] = {"5k": 5000, "10k": 10000, "half": 21097, "marathon": 42195}


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
    start_time: str | None = None
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


SessionStatus = Literal["planned", "done", "skipped", "moved", "dropped"]


class Session(PlannedSession):
    """A planned session as stored, with its database id and status."""

    id: int
    plan_id: int
    status: SessionStatus = "planned"


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
    distance: Distance
    goal_date: date
    vdot: float = Field(gt=0)
    runs_per_week: int = Field(default=4, ge=2, le=6)
    climbing_target_per_week: int = Field(default=2, ge=0, le=3)
    current_weekly_km: float = Field(default=0.0, ge=0)
    longest_recent_run_km: float | None = Field(default=None, ge=0)  # last 30 days, if known
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


class Lap(BaseModel):
    """One lap as the watch recorded it; a structured workout records one lap per step."""

    duration_s: float
    distance_m: float | None = None
    avg_speed_mps: float | None = None
    avg_hr: int | None = None
    step_index: int | None = None  # the workout step the lap belongs to, when recorded


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
    laps: list[Lap] = Field(default_factory=list)


class Commitment(BaseModel):
    id: int | None = None
    kind: EventKind
    sport: str | None = None
    title: str
    weekday: int | None = None
    start_date: str | None = None
    end_date: str | None = None
    start_time: str | None = None
    duration_min: int | None = None
    rpe: float | None = None
    severity: int | None = Field(default=None, ge=1, le=3)
    source: str = "manual"
    external_id: str | None = None
    active: bool = True
    minimal_plan: bool = False

    @model_validator(mode="after")
    def validate_minimal_plan(self) -> Commitment:
        if self.minimal_plan and self.kind != "block":
            raise ValueError("minimal_plan is only valid for block events")
        return self


def commitment_problem(c: Commitment) -> str | None:
    """Explain why a hand-entered commitment is incomplete or invalid, or return None."""
    if c.weekday is not None and not 0 <= c.weekday <= 6:
        return "Weekday must be 0-6, where 0 is Monday."
    try:
        start = date.fromisoformat(c.start_date) if c.start_date else None
        end = date.fromisoformat(c.end_date) if c.end_date else None
    except ValueError:
        return "Dates must use YYYY-MM-DD format."
    if start and end and end < start:
        return "The end date must not precede the start date."
    if c.kind == "fixed":
        if c.weekday is None and start is None:
            return "A fixed commitment needs a weekday or a date."
        if not (c.sport and c.start_time and c.duration_min):
            return "A fixed commitment needs a sport, start time and duration."
    elif c.kind == "block" and not (start and end):
        return "A block needs a start and an end date."
    elif c.kind in {"penalty", "credit"} and start is None:
        return f"A {c.kind} event needs a date."
    if c.kind == "penalty" and c.severity is None:
        return "A penalty event needs a severity (1-3)."
    if c.kind == "credit" and (c.rpe is None or not c.duration_min):
        return "A credit event needs an RPE and a duration."
    return None


class CalendarEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    external_id: str
    title: str | None = None
    starts_at: str
    ends_at: str
    all_day: bool = False
    fetched_at: str | None = None


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
    fever: bool = False  # fever or chest symptoms: rest, not easy running
    resting_hr: int | None = None
    notes: str | None = None


class GarminPush(BaseModel):
    """A workout pushed to Garmin Connect for a session."""

    session_id: int
    workout_id: str
    schedule_id: str | None
    date: date
    hash: str
    pushed_at: str
