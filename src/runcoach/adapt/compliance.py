"""Score a recorded run against its planned steps, lap by lap (pure).

A watch following a structured workout records one lap per executed step, so the laps line
up with the planned steps once repeats are expanded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from runcoach.config import ComplianceConfig
from runcoach.domain.models import Activity, Session, Step

Verdict = Literal["on", "fast", "slow", "untargeted"]
_LABELS = {"warmup": "Warm-up", "cooldown": "Cool-down", "work": "Rep", "recover": "Recovery",
           "steady": "Run"}  # fmt: skip


@dataclass(frozen=True)
class StepScore:
    label: str
    pace_s_per_km: float | None  # what was run
    target: tuple[float, float] | None  # (fast, slow) s/km
    verdict: Verdict


@dataclass(frozen=True)
class Compliance:
    steps: list[StepScore]

    def count(self, verdict: Verdict) -> int:
        return sum(s.verdict == verdict for s in self.steps)

    @property
    def targeted(self) -> int:
        return len(self.steps) - self.count("untargeted")

    def hit(self, cfg: ComplianceConfig) -> bool:
        return self.targeted > 0 and self.count("on") / self.targeted >= cfg.on_target_share

    def summary(self, cfg: ComplianceConfig) -> str:
        if not self.targeted:
            return "No pace targets in this session."
        text = f"{self.count('on')} of {self.targeted} targeted steps on pace"
        extras = [f"{n} {v}" for v in ("fast", "slow") if (n := self.count(v))]
        verdict = "Workout hit." if self.hit(cfg) else "Workout missed its targets."
        return f"{text}{' (' + ', '.join(extras) + ')' if extras else ''}. {verdict}"


def executed_steps(steps: list[Step]) -> list[Step]:
    """Steps in the order the watch runs them, with repeats expanded."""
    flat: list[Step] = []
    for step in steps:
        if step.kind == "repeat":
            flat += [child for _ in range(step.repeats or 1) for child in step.children]
        else:
            flat.append(step)
    return flat


def score(session: Session, activity: Activity, cfg: ComplianceConfig) -> Compliance | None:
    """Per-step verdicts, or None when the laps don't line up with the planned steps."""
    steps = executed_steps(session.steps)
    # shortcut: pairs laps to steps by count; a manual lap press breaks it. Upgrade to
    # lap.step_index pairing once real Garmin files show how repeats are indexed.
    if not steps or len(activity.laps) != len(steps):
        return None
    scores: list[StepScore] = []
    for step, lap in zip(steps, activity.laps):
        pace = 1000 / lap.avg_speed_mps if lap.avg_speed_mps else None
        label = _LABELS.get(step.kind, step.kind)
        # Warm-ups, cool-downs and recoveries are run by feel; only the main work is judged.
        if (
            step.kind not in {"work", "steady"}
            or step.pace_min_s_per_km is None
            or step.pace_max_s_per_km is None
            or pace is None
        ):
            scores.append(StepScore(label, pace, None, "untargeted"))
            continue
        fast, slow = step.pace_min_s_per_km, step.pace_max_s_per_km
        tolerance = cfg.pace_tolerance_s_per_km
        verdict: Verdict = (
            "fast" if pace < fast - tolerance else "slow" if pace > slow + tolerance else "on"
        )
        scores.append(StepScore(label, pace, (fast, slow), verdict))
    return Compliance(scores)
