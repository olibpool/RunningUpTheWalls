from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
from configs import defaults
from pytest_httpx import HTTPXMock

from runcoach import db
from runcoach.config import ConfigNode, load_scheduling_config
from runcoach.domain.models import PlannedSession
from runcoach.ingest.inbox import ingest_file, ingest_inbox
from runcoach.sync import run_sync

GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
<trkpt lat="51.5074" lon="-0.1278"><time>2026-11-02T06:30:00Z</time></trkpt>
<trkpt lat="51.5300" lon="-0.1278"><time>2026-11-02T06:36:00Z</time></trkpt>
</trkseg></trk></gpx>"""
SECRET_URL = "https://calendar.example/private-abc123/basic.ics"


def _cfg(tmp_path: Path) -> ConfigNode:
    cfg = defaults()
    cfg.inbox.path = str(tmp_path / "inbox")
    return cfg


def _plan(conn, day: date, start: str = "06:30", minutes: int = 6) -> int:
    session = PlannedSession(
        date=day, start_time=start, sport="run", kind="easy", title="Easy run",
        duration_min=minutes, rpe=3.0, leg_load=3.0 * minutes,
    )  # fmt: skip
    plan_id = db.save_plan(conn, [session])
    return int(conn.execute("SELECT id FROM session WHERE plan_id = ?", (plan_id,)).fetchone()[0])


def test_ingest_file_is_idempotent_and_estimates_load(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "s.db")
    cfg = load_scheduling_config(_cfg(tmp_path))
    first = ingest_file(conn, GPX, "run.gpx", cfg)
    again = ingest_file(conn, GPX, "copy.gpx", cfg)
    assert first.added and not again.added and first.activity_id == again.activity_id
    (activity,) = db.activities(conn)
    assert activity.load == pytest.approx(cfg.default_rpe["run"] * 6)
    with pytest.raises(ValueError, match="Unsupported"):
        ingest_file(conn, b"x", "run.tcx", cfg)
    conn.close()


def test_inbox_moves_files_to_processed_or_failed(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "s.db")
    cfg = load_scheduling_config(_cfg(tmp_path))
    inbox = Path(cfg.inbox.path)
    inbox.mkdir()
    (inbox / "good.gpx").write_bytes(GPX)
    (inbox / "bad.gpx").write_bytes(b"not xml")
    (inbox / "notes.txt").write_text("ignored")

    report = ingest_inbox(conn, cfg)

    assert (report.added, report.duplicates, report.failed) == (1, 0, 1)
    assert (inbox / "processed" / "good.gpx").exists()
    assert (inbox / "failed" / "bad.gpx").exists()
    assert (inbox / "notes.txt").exists()
    conn.close()


def test_failing_forecast_does_not_stop_matching_or_adaptation(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    cfg = _cfg(tmp_path)
    cfg.weather.enabled = True
    Path(cfg.inbox.path).mkdir()
    (Path(cfg.inbox.path) / "run.gpx").write_bytes(GPX)
    conn = db.connect(tmp_path / "s.db")
    matched_id = _plan(conn, date(2026, 11, 2))
    httpx_mock.add_response(status_code=500)

    with httpx.Client() as client:
        report = run_sync(conn, cfg, datetime(2026, 11, 3, 8, tzinfo=timezone.utc), client=client)

    assert report["inbox"].startswith("ok: 1 added")
    assert report["forecast"].startswith("error")
    assert report["matching"] == "ok: 1 matched, 0 unmatched"
    assert report["adaptation"].startswith("ok")
    assert report["calendar"].startswith("skipped")
    session = db.get_session(conn, matched_id)
    assert session is not None and session.status == "done"
    (activity,) = db.activities(conn)
    assert activity.session_id == matched_id
    assert activity.load == pytest.approx(3.0 * 6)  # planned RPE replaces the default estimate
    assert db.get_kv(conn, "sync.matching.last_ok") is not None
    assert db.get_kv(conn, "sync.forecast.last_ok") is None
    conn.close()


def _ics(start: str, end: str) -> bytes:
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:meeting-1\r\n"
        f"DTSTAMP:20261101T000000Z\r\nDTSTART:{start}\r\nDTEND:{end}\r\n"
        "SUMMARY:Work dinner\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()


def test_calendar_change_moves_a_clashing_session_once(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    cfg = _cfg(tmp_path)
    cfg.gcal.ics_url = SECRET_URL
    conn = db.connect(tmp_path / "s.db")
    session_id = _plan(conn, date(2026, 11, 18), start="18:00", minutes=40)  # a Wednesday
    httpx_mock.add_response(content=_ics("20261118T173000Z", "20261118T210000Z"), is_reusable=True)
    now = datetime(2026, 11, 16, 8, tzinfo=timezone.utc)

    with httpx.Client() as client:
        first = run_sync(conn, cfg, now, client=client)
        second = run_sync(conn, cfg, now, client=client)

    assert first["calendar"] == "ok: 1 events, 1 sessions changed"
    assert second["calendar"] == "ok: 1 events, unchanged"
    moved = db.get_session(conn, session_id)
    assert moved is not None and (moved.date, moved.start_time) != (date(2026, 11, 18), "18:00")
    (log,) = [a for a in db.session_adjustments(conn, session_id) if a.rule == "calendar_change"]
    assert "Calendar changed: moved from Wed 18 Nov 18:00" in log.reason
    conn.close()


def test_calendar_errors_never_reveal_the_private_url(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    cfg = _cfg(tmp_path)
    cfg.gcal.ics_url = SECRET_URL
    conn = db.connect(tmp_path / "s.db")
    httpx_mock.add_response(status_code=404)

    with httpx.Client() as client:
        report = run_sync(conn, cfg, datetime(2026, 11, 16, 8, tzinfo=timezone.utc), client=client)

    assert report["calendar"] == "error: calendar fetch failed (HTTPStatusError)"
    assert "private-abc123" not in (db.get_kv(conn, "sync.last_report") or "")
    conn.close()


def test_a_synced_run_replaces_the_done_you_pressed_and_unlinking_undoes_a_wrong_match(
    tmp_path: Path,
) -> None:
    from runcoach.domain.models import Activity
    from runcoach.sync import match_activities

    conn = db.connect(tmp_path / "s.db")
    cfg = load_scheduling_config(_cfg(tmp_path))
    session_id = _plan(conn, date(2026, 11, 2))
    by_hand = Activity(source="manual", sport="run", started_at="2026-11-02T06:00:00+00:00",
                       duration_s=360, rpe=4.0, load=24.0, session_id=session_id)  # fmt: skip
    db.insert_activity(conn, by_hand)  # what the Done button stores
    ingest_file(conn, GPX, "watch.gpx", cfg)  # the same run arrives from the watch
    match_activities(conn, cfg)
    (only,) = db.activities(conn)
    assert only.source != "manual" and only.session_id == session_id
    assert only.rpe == 4.0 and only.load == pytest.approx(4.0 * 6)  # your rating is kept

    assert db.unlink_activity(conn, only.id or 0) == session_id
    assert db.get_session(conn, session_id).status == "planned"  # type: ignore[union-attr]
    assert db.unlink_activity(conn, only.id or 0) is None  # already free
    conn.close()
