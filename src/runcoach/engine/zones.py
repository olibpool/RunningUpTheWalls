from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ZoneRange:
    min_s_per_km: float
    max_s_per_km: float


@dataclass(frozen=True)
class Zones:
    easy: ZoneRange
    marathon: ZoneRange
    threshold: ZoneRange
    interval: ZoneRange
    repetition: ZoneRange


def _pace_for_fraction(vdot: float, fraction: float) -> float:
    from runcoach.engine.vdot import velocity_at_vo2

    return 60000.0 / velocity_at_vo2(fraction * vdot)


def zones_from_vdot(vdot: float, cfg: object) -> Zones:
    easy_fast = _pace_for_fraction(vdot, 0.72)
    easy_slow = _pace_for_fraction(vdot, 0.62)
    marathon = _pace_for_fraction(vdot, 0.80)
    threshold = _pace_for_fraction(vdot, 0.86)
    interval = _pace_for_fraction(vdot, 0.97)
    repetition = _pace_for_fraction(vdot, 1.05)

    return Zones(
        easy=ZoneRange(min_s_per_km=easy_fast, max_s_per_km=easy_slow),
        marathon=ZoneRange(min_s_per_km=marathon, max_s_per_km=marathon),
        threshold=ZoneRange(min_s_per_km=threshold, max_s_per_km=threshold),
        interval=ZoneRange(min_s_per_km=interval, max_s_per_km=interval),
        repetition=ZoneRange(min_s_per_km=repetition, max_s_per_km=repetition),
    )
