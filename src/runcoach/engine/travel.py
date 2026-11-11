from __future__ import annotations

from datetime import date

from runcoach.config import ConfigNode, SchedulingConfig, load_scheduling_config
from runcoach.domain.models import PlannedSession, Step


def minimal_travel_plan(
    week_start: date,
    typical_easy_duration_min: int,
    cfg: ConfigNode | SchedulingConfig,
) -> list[PlannedSession]:
    if week_start.weekday() != 0:
        raise ValueError("week_start must be a Monday")
    if typical_easy_duration_min <= 0:
        raise ValueError("typical_easy_duration_min must be positive")
    typed_cfg = cfg if isinstance(cfg, SchedulingConfig) else load_scheduling_config(cfg)
    minimal = typed_cfg.minimal_plan
    duration_min = max(1, round(typical_easy_duration_min * minimal.duration_share))
    sessions: list[PlannedSession] = []
    for index in range(minimal.runs):
        steps: list[Step] = []
        title = "Easy travel run"
        reason = "Minimal run during travel."
        if index == 0:
            title = "Easy travel run with strides"
            reason = "Minimal run during travel; strides maintain speed."
            steps.append(
                Step(
                    kind="repeat",
                    repeats=minimal.strides_repeats,
                    children=[
                        Step(
                            kind="work",
                            duration_s=minimal.stride_duration_s,
                            rpe=minimal.strides_rpe,
                        ),
                        Step(kind="recover", duration_s=minimal.stride_recovery_s),
                    ],
                )
            )
        sessions.append(
            PlannedSession(
                date=week_start,
                sport="run",
                kind="easy",
                title=title,
                steps=steps,
                duration_min=duration_min,
                rpe=minimal.easy_rpe,
                leg_load=duration_min * minimal.easy_rpe,
                reason=reason,
            )
        )
    return sessions
