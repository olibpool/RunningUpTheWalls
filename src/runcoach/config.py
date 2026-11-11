from __future__ import annotations

import os
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict


class ConfigNode(SimpleNamespace):
    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        raise AttributeError(f"Config key '{name}' is missing")

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    @classmethod
    def from_mapping(cls, data: object) -> ConfigNode | list["ConfigNode"] | object:
        if isinstance(data, dict):
            result = cls()
            for key, value in data.items():
                setattr(result, key, cls.from_mapping(value))
            return result
        if isinstance(data, list):
            return [cls.from_mapping(item) for item in data]
        return data


class LoadFactorsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_leg: float
    football_leg: float
    climb_leg: float
    climb_finger_power: float
    climb_finger_volume: float
    climb_finger_technique: float


class ClimbingDefaultsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    power: tuple[float, int]
    volume: tuple[float, int]
    technique: tuple[float, int]


class AvailabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: list[tuple[str, str]]
    weekend: list[tuple[str, str]]
    buffer_minutes: int


class SchedulerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_hours_between_hard_leg: int
    relaxed_hours_between_hard_leg: int
    min_hours_between_limit_climbs: int
    min_hours_same_day_gap: int
    max_run_volume_reduction: float


class CalendarKeywordsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixed: list[str]
    penalty: list[str]
    block: list[str]


class SchedulingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    load_factors: LoadFactorsConfig
    climbing_defaults: ClimbingDefaultsConfig
    availability: AvailabilityConfig
    scheduler: SchedulerConfig
    calendar_keywords: CalendarKeywordsConfig


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _set_nested(target: dict[str, Any], path: list[str], value: Any) -> None:
    current = target
    for part in path[:-1]:
        if part not in current or not isinstance(current[part], dict):
            raise ValueError(f"Unknown config key: {'__'.join(path)}")
        current = current[part]
    last = path[-1]
    if last not in current and path[-1] not in current:
        raise ValueError(f"Unknown config key: {'__'.join(path)}")
    current[last] = value


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _apply_env_overrides(config: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    for key, value in env.items():
        if not key.startswith("RUNCOACH_"):
            continue
        trimmed = key[len("RUNCOACH_") :]
        if not trimmed:
            continue
        path = [segment.lower() for segment in trimmed.split("__") if segment]
        if not path:
            continue
        if len(path) == 1 and path[0] == "section":
            continue
        current = config
        for part in path[:-1]:
            if part not in current or not isinstance(current[part], dict):
                raise ValueError(f"Unknown config key: {'__'.join(path)}")
            current = current[part]
        final = path[-1]
        if final not in current:
            raise ValueError(f"Unknown config key: {'__'.join(path)}")
        current[final] = _coerce_env_value(value)
    return config


def _coerce_env_value(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _to_mapping(value: object) -> object:
    if isinstance(value, ConfigNode):
        return {key: _to_mapping(item) for key, item in vars(value).items()}
    if isinstance(value, list):
        return [_to_mapping(item) for item in value]
    return value


def load_scheduling_config(config: ConfigNode) -> SchedulingConfig:
    """Validate the typed multi-sport scheduling configuration."""
    data = _to_mapping(config)
    if not isinstance(data, dict):
        raise TypeError("config must be a ConfigNode mapping")
    return SchedulingConfig.model_validate(
        {
            "load_factors": data.get("load_factors"),
            "climbing_defaults": data.get("climbing_defaults"),
            "availability": data.get("availability"),
            "scheduler": data.get("scheduler"),
            "calendar_keywords": data.get("calendar_keywords"),
        }
    )


def load_config(
    defaults_path: Path | str | None = None,
    local_path: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> ConfigNode:
    base_dir = Path(__file__).resolve().parents[2]
    defaults_file = Path(defaults_path) if defaults_path else base_dir / "config" / "defaults.toml"
    local_file = Path(local_path) if local_path else base_dir / "config" / "local.toml"
    env_map = env if env is not None else dict(os.environ)

    config = _load_toml(defaults_file)
    local_cfg = _load_toml(local_file)
    config = _merge(config, local_cfg)
    config = _apply_env_overrides(config, env_map)
    result = ConfigNode.from_mapping(config)
    assert isinstance(result, ConfigNode)
    return result
