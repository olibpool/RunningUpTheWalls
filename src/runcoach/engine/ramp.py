from __future__ import annotations

from dataclasses import dataclass

from runcoach.config import ConfigNode, SchedulingConfig, load_scheduling_config


@dataclass(frozen=True)
class RampPlan:
    weekly_multipliers: tuple[float, ...]
    no_quality_weeks: int = 0


def ramp_for_block(
    days_off: int,
    cfg: ConfigNode | SchedulingConfig,
) -> RampPlan:
    if days_off <= 0:
        raise ValueError("days_off must be positive")
    typed_cfg = cfg if isinstance(cfg, SchedulingConfig) else load_scheduling_config(cfg)
    ramp = typed_cfg.return_ramp
    if days_off <= ramp.short_days:
        return RampPlan(weekly_multipliers=tuple(ramp.short))
    if days_off <= ramp.medium_days:
        return RampPlan(weekly_multipliers=tuple(ramp.medium))
    return RampPlan(
        weekly_multipliers=tuple(ramp.long),
        no_quality_weeks=ramp.no_quality_weeks_after_long,
    )
