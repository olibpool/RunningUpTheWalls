"""Heat adjustment (pure): slow pace targets by forecast apparent temperature."""

from __future__ import annotations

from datetime import date, timedelta

from runcoach.adapt.rules import Change
from runcoach.config import HeatConfig, SchedulingConfig
from runcoach.domain.models import Session, Step
from runcoach.engine.load import session_load

Forecast = dict[str, float]  # local "YYYY-MM-DDTHH:00" -> apparent temperature in °C
_QUALITY = {"tempo", "intervals", "time_trial"}


def pace_penalty_pct(apparent_temp_c: float, cfg: HeatConfig) -> float:
    """Slowdown fraction, interpolated linearly between the configured points."""
    points = cfg.points
    if apparent_temp_c <= points[0][0]:
        return points[0][1]
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        if apparent_temp_c <= t1:
            return p0 + (p1 - p0) * (apparent_temp_c - t0) / (t1 - t0)
    return points[-1][1]


def _slowed(step: Step, factor: float) -> Step:
    return step.model_copy(
        update={
            "pace_min_s_per_km": step.pace_min_s_per_km * factor
            if step.pace_min_s_per_km
            else None,
            "pace_max_s_per_km": step.pace_max_s_per_km * factor
            if step.pace_max_s_per_km
            else None,
            "children": [_slowed(child, factor) for child in step.children],
        }
    )


def adjust_for_heat(
    session: Session, forecast: Forecast, today: date, cfg: SchedulingConfig
) -> Change | None:
    """Slow a planned run in the next few days when it will feel hot; very hot quality goes easy."""
    heat = cfg.heat_pace_penalty_pct
    if not (
        session.status == "planned"
        and session.sport == "run"
        and session.start_time
        and today <= session.date <= today + timedelta(days=heat.days_ahead)
    ):
        return None
    hour = f"{session.date.isoformat()}T{session.start_time[:2]}:00"
    temp = forecast.get(hour)
    if temp is None:
        return None
    penalty = pace_penalty_pct(temp, heat)
    if penalty <= 0:
        return None

    reason = (
        f"Forecast feels like {temp:.0f}°C at {session.start_time}: paces slowed {penalty:.0%}."
    )
    updates: dict[str, object] = {"steps": [_slowed(s, 1 + penalty) for s in session.steps]}
    if temp > heat.quality_to_easy_above_c and session.kind in _QUALITY:
        minutes = session.duration_min or 0
        rpe = cfg.rpe.easy
        updates = {
            "kind": "easy",
            "title": "Easy run (heat)",
            "steps": [Step(kind="steady", duration_s=minutes * 60, rpe=rpe)],
            "rpe": rpe,
            "leg_load": session_load("run", "easy", rpe, minutes, cfg.load_factors).leg,
        }
        reason += " Too hot for quality: run easy by feel."
    if temp > heat.suggest_rest_or_indoor_above_c:
        reason += " Consider running indoors or resting instead."
    return Change(session.id, "modify", updates, "heat", reason)
