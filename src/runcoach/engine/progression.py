from __future__ import annotations

from dataclasses import dataclass

from runcoach.config import SchedulingConfig
from runcoach.domain.models import Distance


@dataclass(frozen=True)
class Block:
    index: int
    label: str  # base | build | peak | taper
    weeks: int


def split_blocks(n_weeks: int, distance: Distance, cfg: SchedulingConfig) -> list[Block]:
    """Split the weeks into base, build, peak and taper blocks."""
    taper = min(len(cfg.taper_factors[distance]), max(0, n_weeks - 1))
    pre_taper = n_weeks - taper
    base = round(pre_taper * cfg.blocks.base)
    build = round(pre_taper * cfg.blocks.build)
    peak = pre_taper - base - build
    sizes = [("base", base), ("build", build), ("peak", peak), ("taper", taper)]
    return [
        Block(index=index, label=label, weeks=weeks)
        for index, (label, weeks) in enumerate(sizes)
        if weeks > 0
    ]


def is_cutback_week(week_index: int, cfg: SchedulingConfig) -> bool:
    """Return True for every Nth pre-taper week (0-based index)."""
    return (week_index + 1) % cfg.progression.cutback_every_n_weeks == 0


def weekly_targets(
    start_km: float, n_weeks: int, distance: Distance, cfg: SchedulingConfig
) -> list[float]:
    """Weekly km: capped growth, cut-back weeks, the per-distance peak cap, then the taper."""
    taper_factors = cfg.taper_factors[distance]
    taper = min(len(taper_factors), max(0, n_weeks - 1))
    peak_cap = cfg.peak_weekly_km[distance]
    level = float(start_km)
    targets: list[float] = []
    for week in range(n_weeks - taper):
        if is_cutback_week(week, cfg):
            targets.append(round(level * cfg.progression.cutback_factor, 2))
            continue  # a cut-back week does not advance the underlying level
        level = min(level * (1.0 + cfg.progression.max_weekly_increase), peak_cap)
        targets.append(round(level, 2))
    peak = level
    targets.extend(
        round(peak * factor, 2) for factor in taper_factors[len(taper_factors) - taper :]
    )
    return targets
