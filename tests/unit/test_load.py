from __future__ import annotations

import pytest

from runcoach.config import load_config, load_scheduling_config
from runcoach.engine.load import Load, session_load


def _load_factors(tmp_path):
    return load_scheduling_config(
        load_config(local_path=tmp_path / "local.toml", env={})
    ).load_factors


def test_scheduling_config_loads_typed_defaults(tmp_path) -> None:
    config = load_scheduling_config(load_config(local_path=tmp_path / "local.toml", env={}))

    assert config.scheduler.min_hours_between_hard_leg == 48
    assert config.availability.weekday[0] == ("06:30", "08:00")
    assert config.climbing_defaults.power == (7.0, 75)
    assert config.calendar_keywords.block == ["holiday", "annual leave", "ooo"]


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
    tmp_path,
    sport: str,
    kind: str,
    rpe: float,
    minutes: int,
    expected: Load,
) -> None:
    factors = _load_factors(tmp_path)

    assert session_load(sport, kind, rpe, minutes, factors) == expected


def test_session_load_rejects_unknown_sport_or_climbing_kind(tmp_path) -> None:
    factors = _load_factors(tmp_path)

    with pytest.raises(ValueError, match="Unsupported sport"):
        session_load("other", "easy", 3.0, 30, factors)
    with pytest.raises(ValueError, match="Unsupported climbing"):
        session_load("climb", "unknown", 3.0, 30, factors)
