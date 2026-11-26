"""Build small FIT activity files for tests (no real watch file is checked in)."""

from __future__ import annotations

from datetime import datetime

from runcoach.integrations.fit import Field, Message, encode, fit_time

SPORTS = {"running": 1, "soccer": 7, "rock_climbing": 31, "generic": 0}


def activity_fit(
    start: datetime,
    laps: list[tuple[float, float]],  # (seconds, metres) per lap
    *,
    sport: str = "running",
    sub_sport: int = 0,
    avg_hr: int = 150,
    step_indexes: list[int] | None = None,
) -> bytes:
    """A FIT activity file with one session and the given laps."""
    total_s = sum(s for s, _ in laps)
    total_m = sum(m for _, m in laps)
    lap_messages: list[Message] = []
    for i, (seconds, metres) in enumerate(laps):
        fields: list[Field] = [
            (2, "uint32", fit_time(start)),
            (8, "uint32", round(seconds * 1000)),
            (9, "uint32", round(metres * 100)),
            (13, "uint16", round(metres / seconds * 1000)),
            (15, "uint8", avg_hr),
        ]
        if step_indexes is not None:
            fields.append((71, "uint16", step_indexes[i]))
        lap_messages.append((19, fields))
    return encode([
        (0, [(0, "enum", 4), (1, "uint16", 1), (4, "uint32", fit_time(start))]),
        *lap_messages,
        (18, [
            (2, "uint32", fit_time(start)),
            (5, "enum", SPORTS[sport]),
            (6, "enum", sub_sport),
            (8, "uint32", round(total_s * 1000)),
            (9, "uint32", round(total_m * 100)),
            (16, "uint8", avg_hr),
        ]),
    ])  # fmt: skip
