from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from runcoach.config import SchedulingConfig
from runcoach.domain.models import PlannedSession, Step
from runcoach.engine.load import session_load
from runcoach.engine.zones import ZoneRange, Zones

RUN_KINDS = ("easy", "recovery", "long", "tempo", "intervals")


@dataclass(frozen=True)
class WeekContext:
    date: date
    minutes: int  # target total duration of the session
    progress: float = 0.0  # 0 at the start of build, 1 at the end of peak
    strides: bool = False  # append strides to an easy run


def _lerp(bounds: tuple[float, float], progress: float) -> float:
    low, high = bounds
    return low + (high - low) * min(1.0, max(0.0, progress))


def _paced(
    kind: Literal["warmup", "work", "recover", "cooldown", "steady"],
    seconds: int,
    zone: ZoneRange,
    rpe: float | None = None,
) -> Step:
    return Step(
        kind=kind,
        duration_s=seconds,
        pace_min_s_per_km=zone.min_s_per_km,
        pace_max_s_per_km=zone.max_s_per_km,
        rpe=rpe,
    )


def _seconds(steps: list[Step]) -> int:
    total = 0
    for step in steps:
        if step.kind == "repeat":
            total += (step.repeats or 0) * _seconds(step.children)
        else:
            total += step.duration_s or 0
    return total


def _metres(steps: list[Step]) -> float:
    total = 0.0
    for step in steps:
        if step.kind == "repeat":
            total += (step.repeats or 0) * _metres(step.children)
        elif step.duration_s and step.pace_min_s_per_km and step.pace_max_s_per_km:
            mid_pace = (step.pace_min_s_per_km + step.pace_max_s_per_km) / 2
            total += step.duration_s / mid_pace * 1000
    return total


def quality_minutes(kind: str, progress: float, cfg: SchedulingConfig) -> int:
    """Total minutes of a tempo or interval session at a given block progress."""
    t = cfg.templates
    if kind == "tempo":
        work = round(_lerp(t.tempo_work_min, progress))
    elif kind == "intervals":
        work = (
            2
            * round(_lerp(t.interval_rep_min, progress))
            * round(_lerp(t.interval_repeats, progress))
        )
    else:
        raise ValueError(f"{kind} is not a quality session")
    return t.warmup_min + work + t.cooldown_min


def build_session(
    kind: str, ctx: WeekContext, zones: Zones, cfg: SchedulingConfig
) -> PlannedSession:
    """Build a run session as structured steps with pace targets."""
    t = cfg.templates
    warmup = _paced("warmup", t.warmup_min * 60, zones.easy)
    cooldown = _paced("cooldown", t.cooldown_min * 60, zones.easy)
    if kind in {"easy", "recovery", "long"}:
        steps = [_paced("steady", ctx.minutes * 60, zones.easy)]
        title = {"easy": "Easy run", "recovery": "Recovery run", "long": "Long run"}[kind]
        if ctx.strides and kind == "easy":
            stride = _paced("work", t.stride_s, zones.repetition, cfg.rpe.strides)
            jog = Step(kind="recover", duration_s=t.stride_recovery_s)
            steps.append(Step(kind="repeat", repeats=t.strides_repeats, children=[stride, jog]))
            title = "Easy run + strides"
        rpe = getattr(cfg.rpe, kind)
    elif kind == "tempo":
        low, high = t.tempo_work_min
        work = min(high, max(low, ctx.minutes - t.warmup_min - t.cooldown_min))
        steps = [warmup, _paced("work", work * 60, zones.threshold), cooldown]
        title = "Tempo"
        rpe = cfg.rpe.tempo
    elif kind == "intervals":
        rep = round(_lerp(t.interval_rep_min, ctx.progress))
        low, high = t.interval_repeats
        reps = min(high, max(low, round((ctx.minutes - t.warmup_min - t.cooldown_min) / (2 * rep))))
        children = [
            _paced("work", rep * 60, zones.interval),
            _paced("recover", rep * 60, zones.easy),
        ]
        steps = [warmup, Step(kind="repeat", repeats=reps, children=children), cooldown]
        title = "Intervals"
        rpe = cfg.rpe.intervals
    else:
        raise ValueError(f"Unsupported run session kind: {kind}")

    minutes = round(_seconds(steps) / 60)
    return PlannedSession(
        date=ctx.date,
        sport="run",
        kind=kind,
        title=title,
        steps=steps,
        duration_min=minutes,
        distance_m=round(_metres(steps)),
        rpe=rpe,
        leg_load=session_load("run", kind, rpe, minutes, cfg.load_factors).leg,
    )
