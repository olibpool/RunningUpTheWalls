from __future__ import annotations

from dataclasses import dataclass

from runcoach.config import ZonesConfig
from runcoach.engine.vdot import velocity_at_vo2


@dataclass(frozen=True)
class ZoneRange:
    min_s_per_km: float  # fastest acceptable
    max_s_per_km: float  # slowest acceptable


@dataclass(frozen=True)
class Zones:
    easy: ZoneRange
    marathon: ZoneRange
    threshold: ZoneRange
    interval: ZoneRange
    repetition: ZoneRange


def _pace(vdot: float, fraction: float) -> float:
    return 60000.0 / velocity_at_vo2(fraction * vdot)


def _single(vdot: float, fraction: float) -> ZoneRange:
    pace = _pace(vdot, fraction)
    return ZoneRange(min_s_per_km=pace, max_s_per_km=pace)


def zones_from_vdot(vdot: float, cfg: ZonesConfig) -> Zones:
    """Return pace ranges in seconds per km for each training zone."""
    easy_low, easy_high = cfg.easy
    return Zones(
        easy=ZoneRange(min_s_per_km=_pace(vdot, easy_high), max_s_per_km=_pace(vdot, easy_low)),
        marathon=_single(vdot, cfg.marathon),
        threshold=_single(vdot, cfg.threshold),
        interval=_single(vdot, cfg.interval),
        repetition=_single(vdot, cfg.repetition),
    )
