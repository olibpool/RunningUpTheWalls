from __future__ import annotations

from datetime import date, timedelta

from runcoach.domain.models import PlanInput, PlannedSession
from runcoach.engine.progression import weekly_targets


class InfeasibleGoal(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _required_weeks_for_distance(distance_km: float) -> int:
    if distance_km <= 5.0:
        return 6
    if distance_km <= 10.0:
        return 8
    if distance_km <= 21.1:
        return 10
    return 12


def generate_race_plan(inp: PlanInput, today: date, cfg: object) -> list[PlannedSession]:
    if inp.runs_per_week < 2 or inp.runs_per_week > 6:
        raise InfeasibleGoal("Run count is outside the supported range")

    min_weeks = _required_weeks_for_distance(inp.distance_km)
    weeks_available = max(1, (inp.goal_date - today).days // 7)
    if weeks_available < min_weeks:
        suggested_date = today + timedelta(weeks=min_weeks)
        raise InfeasibleGoal(
            f"Not enough weeks to build a safe plan for {inp.distance_km:.1f} km; "
            f"move the goal to {suggested_date.isoformat()} or later."
        )

    max_allowed_growth = 1.10
    projected = inp.current_weekly_km
    for _ in range(weeks_available):
        projected *= max_allowed_growth
    if projected < inp.distance_km * 0.6:
        suggested_date = today + timedelta(weeks=min_weeks)
        raise InfeasibleGoal(
            f"Reaching the target volume from {inp.current_weekly_km} km would need growth "
            f"above the cap; choose a later date such as {suggested_date.isoformat()}."
        )

    target_km_by_week = weekly_targets(inp.current_weekly_km, weeks_available, "half", cfg)
    sessions: list[PlannedSession] = []
    for week_index, weekly_target in enumerate(target_km_by_week):
        session_date = today + timedelta(weeks=week_index)
        quality_days = max(1, min(inp.runs_per_week - 1, 2))
        max(0, inp.runs_per_week - quality_days - 1)
        for run_index in range(inp.runs_per_week):
            if run_index == 0:
                kind = "long"
                title = "Long run"
                duration_min = 60
                distance_m = int(weekly_target * 1000 * 0.28)
                rpe = 4.0
            elif run_index == 1:
                kind = "tempo"
                title = "Tempo"
                duration_min = 35
                distance_m = int(weekly_target * 1000 * 0.18)
                rpe = 6.0
            else:
                kind = "easy"
                title = "Easy run"
                duration_min = 30 + run_index * 5
                distance_m = int(weekly_target * 1000 * 0.12)
                rpe = 3.0
            sessions.append(
                PlannedSession(
                    date=session_date + timedelta(days=run_index * 2),
                    sport="run",
                    kind=kind,
                    title=title,
                    steps=[],
                    duration_min=duration_min,
                    distance_m=max(2000, distance_m),
                    rpe=rpe,
                    leg_load=float(duration_min) * rpe,
                    finger_load=0.0,
                    reason="Generated from the race plan",
                )
            )

    return sessions[: len(target_km_by_week) * inp.runs_per_week]
