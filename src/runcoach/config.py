from __future__ import annotations

import os
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from runcoach.domain.models import Distance, Sport


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
    quality_gap_target_hours: int
    long_run_weekday_cost: int
    limit_climb_before_long_cost: int
    hard_run_climb_same_day_cost: int
    same_day_climb_cost: int
    same_day_session_cost: int
    adjacent_run_day_cost: int
    climbing_spacing_days: int
    drop_cost_easy: int
    drop_cost_climb: int
    drop_cost_quality: int
    drop_cost_other: int


class CalendarKeywordsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixed: list[str]
    penalty: list[str]
    block: list[str]


class ProgressionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_weekly_increase: float
    cutback_every_n_weeks: int
    cutback_factor: float
    max_long_run_share: float
    max_long_run_minutes: int
    min_rest_days_per_week: int


class ContinuousConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekly_increase: float = Field(ge=0)
    weeks_ahead: int = Field(gt=0)
    replan_interval_days: int = Field(gt=0)
    chronic_load_weeks: int = Field(gt=0)
    block_cycle: list[Literal["aerobic", "threshold", "speed"]]
    weeks_per_block: int = Field(gt=0)
    retest_every_weeks: int = Field(gt=0)
    retest_distance_m: int = Field(gt=0)
    acr_low: float = Field(gt=0)
    acr_high: float = Field(gt=0)
    acr_hold: float = Field(gt=0)
    quality_load_share: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_acr_thresholds(self) -> ContinuousConfig:
        if not self.block_cycle:
            raise ValueError("continuous.block_cycle must not be empty")
        if self.acr_low >= self.acr_high:
            raise ValueError("continuous.acr_low must be below acr_high")
        if self.acr_hold < self.acr_high:
            raise ValueError("continuous.acr_hold must be at least acr_high")
        return self


class PenaltyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_of_max_rpe: float = Field(gt=0, le=10)
    days_after: dict[str, int]
    rhr_delta_bpm: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_days_after(self) -> PenaltyConfig:
        if any(value < 0 for value in self.days_after.values()):
            raise ValueError("penalty.days_after values must not be negative")
        return self


class ReturnRampConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    short_days: int = Field(gt=0)
    short: list[float]
    medium_days: int = Field(gt=0)
    medium: list[float]
    long: list[float]
    no_quality_weeks_after_long: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_profiles(self) -> ReturnRampConfig:
        if self.short_days >= self.medium_days:
            raise ValueError("return_ramp.short_days must be below medium_days")
        for profile in (self.short, self.medium, self.long):
            if not profile or any(not 0 < factor <= 1 for factor in profile):
                raise ValueError("return ramp profiles must contain factors in (0, 1]")
        return self


class MinimalPlanConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runs: int = Field(gt=0, le=7)
    duration_share: float = Field(gt=0, le=1)
    credit_leg_factor: float = Field(ge=0)
    credit_easy_rpe: float = Field(gt=0, le=10)
    easy_rpe: float = Field(gt=0, le=10)
    strides_rpe: float = Field(gt=0, le=10)
    strides_repeats: int = Field(gt=0)
    stride_duration_s: int = Field(gt=0)
    stride_recovery_s: int = Field(gt=0)


class TimeTrialConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warmup_min: int = Field(gt=0)
    cooldown_min: int = Field(gt=0)
    warmup_rpe: float = Field(gt=0, le=10)
    cooldown_rpe: float = Field(gt=0, le=10)
    work_rpe: float = Field(gt=0, le=10)
    estimated_5k_duration_min: int = Field(gt=0)


class ReadinessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rhr_window_days: int = Field(gt=0)
    rhr_min_samples: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_sample_count(self) -> ReadinessConfig:
        if self.rhr_min_samples > self.rhr_window_days:
            raise ValueError("readiness.rhr_min_samples cannot exceed rhr_window_days")
        return self


class ZonesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    easy: tuple[float, float]
    marathon: float
    threshold: float
    interval: float
    repetition: float

    @model_validator(mode="after")
    def validate_order(self) -> ZonesConfig:
        fractions = [*self.easy, self.marathon, self.threshold, self.interval, self.repetition]
        if any(left >= right for left, right in zip(fractions, fractions[1:])):
            raise ValueError("zones fractions must strictly increase from easy to repetition")
        return self


class RpeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recovery: float = Field(gt=0, le=10)
    easy: float = Field(gt=0, le=10)
    strides: float = Field(gt=0, le=10)
    long: float = Field(gt=0, le=10)
    tempo: float = Field(gt=0, le=10)
    intervals: float = Field(gt=0, le=10)


class BlocksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base: float = Field(ge=0)
    build: float = Field(ge=0)
    peak: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> BlocksConfig:
        if abs(self.base + self.build + self.peak - 1.0) > 1e-9:
            raise ValueError("blocks shares must sum to 1")
        return self


class TemplatesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warmup_min: int = Field(gt=0)
    cooldown_min: int = Field(gt=0)
    tempo_work_min: tuple[int, int]
    interval_repeats: tuple[int, int]
    interval_rep_min: tuple[int, int]
    strides_repeats: int = Field(gt=0)
    stride_s: int = Field(gt=0)
    stride_recovery_s: int = Field(gt=0)
    recovery_share_of_easy: float = Field(gt=0, le=1)
    long_share: tuple[float, float]
    min_easy_minutes: int = Field(gt=0)
    max_quality_share: float = Field(gt=0, le=1)


class MatchingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_score: float = Field(gt=0)


class IllnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    niggle_days: int = Field(gt=0)
    ill_days: int = Field(gt=0)
    return_ramp: list[float]


class RetestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality_runs: int = Field(gt=0)
    faster_by: float = Field(gt=0, lt=1)


class HeatConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: list[tuple[float, float]]
    quality_to_easy_above_c: float
    suggest_rest_or_indoor_above_c: float
    days_ahead: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_points(self) -> HeatConfig:
        temps = [t for t, _ in self.points]
        if len(temps) < 2 or temps != sorted(temps):
            raise ValueError("heat_pace_penalty_pct.points need 2+ entries in temperature order")
        return self


class WeatherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timeout_s: float = Field(gt=0)


class InboxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str


class GcalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ics_url: str = Field(repr=False)  # a secret; keep it out of reprs and logs
    days_back: int = Field(ge=0)
    days_ahead: int = Field(gt=0)
    reschedule_days: int = Field(gt=0)


class SchedulingConfig(BaseModel):
    """Typed view of the whole config; unknown sections or keys raise."""

    model_config = ConfigDict(extra="forbid")

    zones: ZonesConfig
    rpe: RpeConfig
    blocks: BlocksConfig
    templates: TemplatesConfig
    race_min_weeks: dict[Distance, int]
    min_peak_weekly_km: dict[Distance, float]
    taper_factors: dict[Distance, list[float]]
    peak_weekly_km: dict[Distance, float]
    load_factors: LoadFactorsConfig
    climbing_defaults: ClimbingDefaultsConfig
    availability: AvailabilityConfig
    scheduler: SchedulerConfig
    calendar_keywords: CalendarKeywordsConfig
    progression: ProgressionConfig
    continuous: ContinuousConfig
    penalty: PenaltyConfig
    return_ramp: ReturnRampConfig
    minimal_plan: MinimalPlanConfig
    time_trial: TimeTrialConfig
    readiness: ReadinessConfig
    default_rpe: dict[Sport, float]
    matching: MatchingConfig
    illness: IllnessConfig
    retest: RetestConfig
    heat_pace_penalty_pct: HeatConfig
    weather: WeatherConfig
    inbox: InboxConfig
    gcal: GcalConfig


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
    return SchedulingConfig.model_validate(data)


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
