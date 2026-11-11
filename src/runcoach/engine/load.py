from __future__ import annotations

from dataclasses import dataclass

from runcoach.config import LoadFactorsConfig


def session_rpe_load(rpe: float, minutes: int | float) -> float:
    return float(rpe) * float(minutes)


@dataclass(frozen=True)
class Load:
    leg: float
    finger: float


def session_load(
    sport: str,
    kind: str,
    rpe: float,
    minutes: int | float,
    cfg: LoadFactorsConfig,
) -> Load:
    """Calculate leg and finger load using injected, validated load factors."""
    base_load = session_rpe_load(rpe, minutes)
    if sport == "run":
        return Load(leg=base_load * cfg.run_leg, finger=0.0)
    if sport == "football":
        return Load(leg=base_load * cfg.football_leg, finger=0.0)
    if sport == "climb":
        finger_factors = {
            "climb_power": cfg.climb_finger_power,
            "climb_volume": cfg.climb_finger_volume,
            "climb_technique": cfg.climb_finger_technique,
        }
        try:
            finger_factor = finger_factors[kind]
        except KeyError as exc:
            raise ValueError(f"Unsupported climbing session kind: {kind}") from exc
        return Load(
            leg=base_load * cfg.climb_leg,
            finger=base_load * finger_factor,
        )
    if sport == "rest":
        return Load(leg=0.0, finger=0.0)
    raise ValueError(f"Unsupported sport for load calculation: {sport}")


def duration_from_distance_and_pace(distance_m: float, pace_s_per_km: float) -> float:
    if pace_s_per_km <= 0:
        raise ValueError("pace_s_per_km must be positive")
    return (distance_m / 1000.0) * pace_s_per_km / 60.0
