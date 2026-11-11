from pathlib import Path

import pytest

from runcoach.config import load_config


def test_config_overrides_follow_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = tmp_path / "defaults.toml"
    defaults.write_text("[app]\nname = 'demo'\n\n[app.inner]\nvalue = 1\n", encoding="utf-8")
    local = tmp_path / "local.toml"
    local.write_text("[app]\nname = 'local'\n\n[app.inner]\nvalue = 2\n", encoding="utf-8")
    monkeypatch.setenv("RUNCOACH_APP__NAME", "env")
    monkeypatch.setenv("RUNCOACH_APP__INNER__VALUE", "3")
    cfg = load_config(defaults_path=defaults, local_path=local)
    assert cfg.app.name == "env"
    assert cfg.app.inner.value == 3


def test_unknown_key_raises() -> None:
    with pytest.raises(ValueError):
        load_config(env={"RUNCOACH_APP__UNKNOWN": "x"})
