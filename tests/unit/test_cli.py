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
    event = next(row for row in db.list_commitments(conn) if row["title"] == "Block")
    assert event["kind"] == "block"
    assert event["minimal_plan"] == 1
    conn.close()
