"""The shipped defaults for tests: no local.toml, no RUNCOACH_* env, no settings-page saves."""

from pathlib import Path

from runcoach.config import ConfigNode, SchedulingConfig, load_config, load_scheduling_config


def defaults() -> ConfigNode:
    """A fresh copy each call, so a test can change it freely."""
    return load_config(local_path=Path("does-not-exist.toml"), env={})


def typed() -> SchedulingConfig:
    return load_scheduling_config(defaults())
