from __future__ import annotations


def session_rpe_load(rpe: float, minutes: int | float) -> float:
    return float(rpe) * float(minutes)


def session_load(sport: str, kind: str, rpe: float, minutes: int | float) -> tuple[float, float]:
    factors = {
        "run": 1.0,
        "football": 1.1,
        "climb": 0.1,
    }
    leg = float(rpe) * float(minutes) * factors.get(sport, 1.0)
    finger = 0.0
    if sport == "climb":
        finger = float(rpe) * float(minutes) * 0.6
    return leg, finger


def duration_from_distance_and_pace(distance_m: float, pace_s_per_km: float) -> float:
    if pace_s_per_km <= 0:
        raise ValueError("pace_s_per_km must be positive")
    return (distance_m / 1000.0) * pace_s_per_km / 60.0
