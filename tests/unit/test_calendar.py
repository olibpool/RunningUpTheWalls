from __future__ import annotations

from datetime import date, datetime, timezone

import httpx

from runcoach import db
from runcoach.config import load_config, load_scheduling_config
from runcoach.domain.models import CalendarEvent
from runcoach.ingest.calendar import (
    busy_slots,
    detect_commitments,
    fetch_ical_events,
    parse_ical_events,
)


def test_recurring_event_preserves_local_time_across_daylight_saving() -> None:
    ics = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:weekly-football
DTSTART;TZID=Europe/London:20260322T090000
DTEND;TZID=Europe/London:20260322T100000
RRULE:FREQ=WEEKLY;COUNT=3
SUMMARY:Football
END:VEVENT
END:VCALENDAR
"""

    events = parse_ical_events(
        ics,
        window_start=date(2026, 3, 22),
        window_end=date(2026, 4, 5),
        fetched_at=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )

    assert [event.title for event in events] == ["Football"] * 3
    assert [event.starts_at for event in events] == [
        "2026-03-22T09:00:00+00:00",
        "2026-03-29T08:00:00+00:00",
        "2026-04-05T08:00:00+00:00",
    ]


def test_parser_skips_cancelled_and_transparent_and_preserves_all_day() -> None:
    ics = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:cancelled
DTSTART:20261115T090000Z
DTEND:20261115T100000Z
STATUS:CANCELLED
SUMMARY:Cancelled
END:VEVENT
BEGIN:VEVENT
UID:free
DTSTART:20261115T110000Z
DTEND:20261115T120000Z
TRANSP:TRANSPARENT
SUMMARY:Free
END:VEVENT
BEGIN:VEVENT
UID:holiday
DTSTART;VALUE=DATE:20261116
DTEND;VALUE=DATE:20261119
SUMMARY:Holiday
END:VEVENT
END:VCALENDAR
"""

    events = parse_ical_events(ics)

    assert len(events) == 1
    assert events[0].title == "Holiday"
    assert events[0].starts_at == "2026-11-16"
    assert events[0].ends_at == "2026-11-19"
    assert events[0].all_day
    assert (
        busy_slots(
            events,
            start="2026-11-15T00:00:00+00:00",
            end="2026-11-20T00:00:00+00:00",
        )
        == []
    )


def test_busy_slots_keep_only_timed_events_overlapping_window() -> None:
    event = CalendarEvent(
        external_id="meeting@2026-11-15T09:00:00+00:00",
        title="Meeting",
        starts_at="2026-11-15T09:00:00+00:00",
        ends_at="2026-11-15T10:00:00+00:00",
    )

    slots = busy_slots(
        [event],
        start="2026-11-15T09:30:00+00:00",
        end="2026-11-15T11:00:00+00:00",
    )

    assert slots == [
        (
            datetime(2026, 11, 15, 9, tzinfo=timezone.utc),
            datetime(2026, 11, 15, 10, tzinfo=timezone.utc),
        )
    ]


def test_fetch_ical_events_uses_injected_http_client() -> None:
    ics = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:event-1
DTSTART:20261115T090000Z
DTEND:20261115T100000Z
SUMMARY:Run
END:VEVENT
END:VCALENDAR
"""
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=ics))

    with httpx.Client(transport=transport) as client:
        events = fetch_ical_events(
            "https://calendar.example/private.ics",
            window_start=date(2026, 11, 15),
            window_end=date(2026, 11, 15),
            client=client,
        )

    assert [event.title for event in events] == ["Run"]


def test_calendar_cache_upserts_and_removes_stale_window_rows(tmp_path) -> None:
    conn = db.connect(tmp_path / "calendar.db")
    old_event = CalendarEvent(
        external_id="old@2026-11-15T09:00:00+00:00",
        title="Old",
        starts_at="2026-11-15T09:00:00+00:00",
        ends_at="2026-11-15T10:00:00+00:00",
    )
    new_event = CalendarEvent(
        external_id="new@2026-11-15T09:00:00+00:00",
        title="New",
        starts_at="2026-11-15T09:00:00+00:00",
        ends_at="2026-11-15T10:00:00+00:00",
    )
    db.cache_calendar_events(
        conn,
        [old_event],
        window_start=date(2026, 11, 15),
        window_end=date(2026, 11, 16),
    )
    db.cache_calendar_events(
        conn,
        [new_event],
        window_start=date(2026, 11, 15),
        window_end=date(2026, 11, 16),
    )

    rows = conn.execute("SELECT external_id, title FROM calendar_event").fetchall()
    conn.close()
    assert [(row["external_id"], row["title"]) for row in rows] == [(new_event.external_id, "New")]


def test_detect_commitments_creates_inactive_proposals(tmp_path) -> None:
    config = load_scheduling_config(load_config(local_path=tmp_path / "local.toml", env={}))
    events = [
        CalendarEvent(
            external_id="football@1",
            title="Football training",
            starts_at="2026-11-17T19:00:00+00:00",
            ends_at="2026-11-17T20:30:00+00:00",
        ),
        CalendarEvent(
            external_id="drinks@1",
            title="Drinks with friends",
            starts_at="2026-11-18T18:00:00+00:00",
            ends_at="2026-11-18T20:00:00+00:00",
        ),
        CalendarEvent(
            external_id="holiday@1",
            title="Trip",
            starts_at="2026-12-20",
            ends_at="2026-12-23",
            all_day=True,
        ),
        CalendarEvent(
            external_id="meeting@1",
            title="Work meeting",
            starts_at="2026-11-19T10:00:00+00:00",
            ends_at="2026-11-19T11:00:00+00:00",
        ),
    ]

    proposals = detect_commitments(events, config.calendar_keywords)

    assert [(proposal.kind, proposal.title) for proposal in proposals] == [
        ("fixed", "Football training"),
        ("penalty", "Drinks with friends"),
        ("block", "Trip"),
    ]
    assert all(proposal.source == "gcal" and not proposal.active for proposal in proposals)
    assert proposals[0].sport == "football"
    assert proposals[0].start_time == "19:00"
    assert proposals[0].duration_min == 90
    assert proposals[2].start_date == "2026-12-20"
    assert proposals[2].end_date == "2026-12-22"
