from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from runcoach.config import ConfigNode, SchedulingConfig, load_scheduling_config
from runcoach.domain.models import Checkin, Commitment, EventKind

MaxIntensity = Literal["blocked", "easy", "normal"]


@dataclass(frozen=True)
class CommitmentOccurrence:
    commitment_id: int | None
    title: str
    sport: str | None
    kind: EventKind
    date: date
    start_time: str | None
    duration_min: int | None
    rpe: float | None
    severity: int | None = None
    minimal_plan: bool = False


@dataclass(frozen=True)
class DayConstraint:
    date: date
    max_intensity: MaxIntensity
    reason: str
    max_rpe: float | None = None
    credit_leg_load: float = 0.0
    sources: frozenset[str] = frozenset()
    easy_sports: frozenset[str] | None = None
    travel_allowed: bool = False


def _normalize_weekday(value: int | None) -> int | None:
    if value is None:
        return None
    if 0 <= value <= 6:
        return value
    if 1 <= value <= 7:
        return value - 1
    raise ValueError(f"Unsupported weekday value: {value}")


def expand_commitments(
    commitments: list[Commitment],
    start: date,
    end: date,
) -> list[CommitmentOccurrence]:
    """Expand recurring and dated commitments into a concrete window of occurrences."""
    occurrences: list[CommitmentOccurrence] = []
    seen: set[tuple[date, str, str | None, str]] = set()
    window_start = start
    window_end = end

    for commitment in commitments:
        if not commitment.active:
            continue

        if commitment.start_date and commitment.end_date:
            dated_start = date.fromisoformat(commitment.start_date)
            dated_end = date.fromisoformat(commitment.end_date)
            current = max(window_start, dated_start)
            finish = min(window_end, dated_end)
            while current <= finish:
                key = (current, commitment.title, commitment.sport, commitment.kind)
                if key not in seen:
                    occurrences.append(
                        CommitmentOccurrence(
                            commitment_id=commitment.id,
                            title=commitment.title,
                            sport=commitment.sport,
                            kind=commitment.kind,
                            date=current,
                            start_time=commitment.start_time,
                            duration_min=commitment.duration_min,
                            rpe=commitment.rpe,
                            severity=commitment.severity,
                            minimal_plan=commitment.minimal_plan,
                        )
                    )
                    seen.add(key)
                current += timedelta(days=1)

        weekday = _normalize_weekday(commitment.weekday)
        if weekday is not None:
            current = window_start
            while current <= window_end:
                if current.weekday() == weekday:
                    key = (current, commitment.title, commitment.sport, commitment.kind)
                    if key not in seen:
                        occurrences.append(
                            CommitmentOccurrence(
                                commitment_id=commitment.id,
                                title=commitment.title,
                                sport=commitment.sport,
                                kind=commitment.kind,
                                date=current,
                                start_time=commitment.start_time,
                                duration_min=commitment.duration_min,
                                rpe=commitment.rpe,
                                severity=commitment.severity,
                                minimal_plan=commitment.minimal_plan,
                            )
                        )
                        seen.add(key)
                current += timedelta(days=1)

    return sorted(occurrences, key=lambda item: (item.date, item.title))


_INTENSITY_RANK: dict[MaxIntensity, int] = {"normal": 0, "easy": 1, "blocked": 2}


def merge_constraint(left: DayConstraint | None, right: DayConstraint) -> DayConstraint:
    """Both limits on one day: the stricter of each, and travel runs only if every block
    on the day allows them."""
    if left is None:
        return right
    rpe_values = [value for value in (left.max_rpe, right.max_rpe) if value is not None]
    easy_sports = (
        None
        if left.easy_sports is None or right.easy_sports is None
        else left.easy_sports | right.easy_sports
    )
    if "block" in left.sources and "block" in right.sources:
        travel_allowed = left.travel_allowed and right.travel_allowed
    else:
        travel_allowed = right.travel_allowed if "block" in right.sources else left.travel_allowed
    return DayConstraint(
        date=left.date,
        max_intensity=max(left.max_intensity, right.max_intensity, key=_INTENSITY_RANK.__getitem__),
        reason="; ".join(dict.fromkeys((left.reason, right.reason))),
        max_rpe=min(rpe_values) if rpe_values else None,
        credit_leg_load=left.credit_leg_load + right.credit_leg_load,
        sources=left.sources | right.sources,
        easy_sports=easy_sports,
        travel_allowed=travel_allowed,
    )


def day_constraints(
    events: list[CommitmentOccurrence],
    checkins: list[Checkin],
    cfg: ConfigNode | SchedulingConfig,
) -> dict[date, DayConstraint]:
    """Derive deterministic intensity limits and credited activity load by date."""
    typed_cfg = load_scheduling_config(cfg)
    constraints: dict[date, DayConstraint] = {}

    def add(
        day: date,
        intensity: MaxIntensity,
        reason: str,
        *,
        max_rpe: float | None = None,
        credit_leg_load: float = 0.0,
        easy_sports: frozenset[str] | None = None,
        travel_allowed: bool = False,
        source: str,
    ) -> None:
        new = DayConstraint(
            date=day, max_intensity=intensity, reason=reason, max_rpe=max_rpe,
            credit_leg_load=credit_leg_load, sources=frozenset({source}),
            easy_sports=easy_sports, travel_allowed=travel_allowed,
        )  # fmt: skip
        constraints[day] = merge_constraint(constraints.get(day), new)

    for event in events:
        if event.kind == "penalty":
            if event.severity is None:
                raise ValueError(f"Penalty event {event.title!r} must have a severity")
            try:
                following_days = typed_cfg.penalty.days_after[str(event.severity)]
            except KeyError as exc:
                raise ValueError(
                    f"No days_after configuration for penalty severity {event.severity}"
                ) from exc
            for offset in range(following_days + 1):
                day = event.date + timedelta(days=offset)
                reason = (
                    f"Penalty {event.title} (severity {event.severity})"
                    if offset == 0
                    else f"Easy day after penalty {event.title}"
                )
                add(
                    day,
                    "easy",
                    reason,
                    max_rpe=typed_cfg.penalty.day_of_max_rpe,
                    source="penalty",
                )
        elif event.kind == "block":
            add(
                event.date,
                "blocked",
                f"Blocked by {event.title}",
                travel_allowed=event.minimal_plan,
                source="block",
            )
        elif event.kind == "credit":
            if event.rpe is None or event.duration_min is None:
                raise ValueError(f"Credit event {event.title!r} must include RPE and duration")
            credited_load = (
                event.rpe * event.duration_min * typed_cfg.minimal_plan.credit_leg_factor
            )
            add(
                event.date,
                "easy" if event.rpe >= typed_cfg.minimal_plan.credit_easy_rpe else "normal",
                f"Credited activity {event.title}",
                credit_leg_load=credited_load,
                easy_sports=frozenset({"run"}),
                source="credit",
            )

    for checkin in checkins:
        if checkin.ill or checkin.niggle:
            details = "illness" if checkin.ill else f"niggle: {checkin.niggle}"
            add(
                checkin.date,
                "easy",
                f"Check-in reports {details}",
                max_rpe=typed_cfg.penalty.day_of_max_rpe,
                source="checkin",
            )

    return constraints
