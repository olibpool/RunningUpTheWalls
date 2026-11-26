from __future__ import annotations

import pytest
from configs import typed

from runcoach.engine.load import Load, session_load


@pytest.mark.parametrize(
    ("sport", "kind", "rpe", "minutes", "expected"),
    [
        ("run", "easy", 3.0, 30, Load(90.0, 0.0)),
        ("football", "football", 6.0, 90, Load(594.0, 0.0)),
        ("climb", "climb_power", 7.0, 75, Load(52.5, 525.0)),
        ("climb", "climb_volume", 5.5, 75, Load(41.25, 247.5)),
        ("climb", "climb_technique", 4.0, 60, Load(24.0, 72.0)),
        ("rest", "rest", 0.0, 0, Load(0.0, 0.0)),
    ],
)
def test_session_load_uses_configured_sport_and_kind_factors(
    sport: str,
    kind: str,
    rpe: float,
    minutes: int,
    expected: Load,
) -> None:
    factors = typed().load_factors

    assert session_load(sport, kind, rpe, minutes, factors) == expected


def test_session_load_rejects_unknown_sport_or_climbing_kind() -> None:
    factors = typed().load_factors

    with pytest.raises(ValueError, match="Unsupported sport"):
        session_load("other", "easy", 3.0, 30, factors)
    with pytest.raises(ValueError, match="Unsupported climbing"):
        session_load("climb", "unknown", 3.0, 30, factors)
