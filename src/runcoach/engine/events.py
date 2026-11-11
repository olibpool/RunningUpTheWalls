from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from runcoach.domain.models import Commitment


@dataclass(frozen=True)
class CommitmentOccurrence:
    commitment_id: int | None
    title: str
    sport: str | None
    kind: str
    date: date
    start_time: str | None
    duration_min: int | None
    rpe: float | None


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
                            )
                        )
                        seen.add(key)
                current += timedelta(days=1)

    return sorted(occurrences, key=lambda item: (item.date, item.title))
