"""Watch export (Phase 4): a neutral workout format and the exporter interface.

The engine never imports this. Library-specific mapping (garminconnect's typed models, a FIT
writer) sits behind WatchExporter once the P4-T1 spike confirms how the watch shows workouts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol

from runcoach.config import ExportConfig
from runcoach.domain.models import Session, Step

StepKind = Literal["warmup", "cooldown", "interval", "recovery", "run"]
_KINDS: dict[str, StepKind] = {
    "warmup": "warmup",
    "cooldown": "cooldown",
    "work": "interval",
    "recover": "recovery",
    "steady": "run",
}


@dataclass(frozen=True)
class WorkoutStep:
    kind: StepKind
    end: Literal["time", "distance", "open"]
    value: float | None  # seconds or metres, matching `end`
    pace_fast_s_per_km: float | None = None
    pace_slow_s_per_km: float | None = None


@dataclass(frozen=True)
class RepeatGroup:
    iterations: int
    steps: list[WorkoutStep]


@dataclass(frozen=True)
class WorkoutSpec:
    name: str
    date: date
    steps: list[WorkoutStep | RepeatGroup] = field(default_factory=list)


def _step(step: Step) -> WorkoutStep:
    if step.duration_s:
        end: Literal["time", "distance", "open"] = "time"
        value: float | None = float(step.duration_s)
    elif step.distance_m:
        end, value = "distance", float(step.distance_m)
    else:
        end, value = "open", None
    return WorkoutStep(
        kind=_KINDS[step.kind],
        end=end,
        value=value,
        # A smaller s/km is faster; keep the bounds explicit for the library mapping.
        pace_fast_s_per_km=step.pace_min_s_per_km,
        pace_slow_s_per_km=step.pace_max_s_per_km,
    )


def to_workout_spec(session: Session, week_number: int) -> WorkoutSpec:
    """Map a run session's steps to the neutral format, named compactly like 'RC W3 Tempo'."""
    if session.sport != "run":
        raise ValueError("Only run sessions are exported to the watch")
    steps: list[WorkoutStep | RepeatGroup] = [
        RepeatGroup(step.repeats or 1, [_step(child) for child in step.children])
        if step.kind == "repeat"
        else _step(step)
        for step in session.steps
    ]
    name = f"RC W{week_number} {session.title.split(' (')[0]}"
    return WorkoutSpec(name=name, date=session.date, steps=steps)


@dataclass(frozen=True)
class PushResult:
    workout_id: str | None
    schedule_id: str | None


@dataclass(frozen=True)
class SyncReport:
    pushed: int = 0
    removed: int = 0
    unchanged: int = 0


class WatchExporter(Protocol):
    def push_session(self, s: Session) -> PushResult: ...

    def remove_session(self, s: Session) -> None: ...

    def sync_window(self, sessions: list[Session], start: date, end: date) -> SyncReport: ...


class NullExporter:
    """The default: nothing is pushed anywhere."""

    def push_session(self, s: Session) -> PushResult:
        return PushResult(None, None)

    def remove_session(self, s: Session) -> None:
        return None

    def sync_window(self, sessions: list[Session], start: date, end: date) -> SyncReport:
        return SyncReport()


def exporter_for(cfg: ExportConfig) -> WatchExporter:
    """Pick the exporter configured in [export] backend."""
    if cfg.backend == "none":
        return NullExporter()
    raise NotImplementedError(
        f"The {cfg.backend} exporter is Phase 4 work that starts with the P4-T1 Garmin spike."
    )
