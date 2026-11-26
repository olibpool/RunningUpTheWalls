from __future__ import annotations

import sys

import pytest

from runcoach import db
from runcoach.cli import main


def test_event_cli_persists_minimal_travel_block(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "events.db"
    connect = db.connect
    conn = connect(db_path)
    monkeypatch.setattr("runcoach.cli.db.connect", lambda: connect(db_path))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runcoach",
            "event",
            "add",
            "--kind",
            "block",
            "--start",
            "2026-12-20",
            "--end",
            "2026-12-27",
            "--minimal-plan",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    conn.close()
    conn = connect(db_path)
    event = next(row for row in db.list_commitments(conn) if row.title == "Block")
    assert event.kind == "block"
    assert event.minimal_plan
    conn.close()


def test_plan_generate_refuses_to_save_an_unsafe_plan(monkeypatch, tmp_path, capsys) -> None:
    from runcoach.engine.plan import PlanGenerationResult
    from runcoach.engine.safety import Violation

    db_path = tmp_path / "plans.db"
    connect = db.connect
    monkeypatch.setattr("runcoach.cli.db.connect", lambda: connect(db_path))
    unsafe = PlanGenerationResult(
        sessions=[], dropped=[], warnings=[], violations=[Violation("weekly-rise", "too much")]
    )
    monkeypatch.setattr("runcoach.cli.generate_race_plan_result", lambda *a, **k: unsafe)
    monkeypatch.setattr(
        sys,
        "argv",
        "runcoach plan generate --distance half --date 2027-03-14 --vdot 50 --runs 4 "
        "--current-km 30 --save".split(),
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert "Not saved" in capsys.readouterr().err
    conn = connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM plan").fetchone()[0] == 0
    conn.close()
