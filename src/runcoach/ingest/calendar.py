from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import recurring_ical_events  # type: ignore[import-untyped]
from icalendar import Calendar

from runcoach.domain.models import CalendarEvent


def _window_datetime(value: date | datetime, tz: ZoneInfo, *, end: bool) -> datetime:
    if isinstance(value, datetime):
        local = value
        if local.tzinfo is None:
            local = local.replace(tzinfo=tz)
        return local.astimezone(timezone.utc)
    local_time = time.max if end else time.min
    return datetime.combine(value, local_time, tzinfo=tz).astimezone(timezone.utc)


def _event_datetime(value: date | datetime, tz: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=tz)
        return value.astimezone(timezone.utc)
    return datetime.combine(value, time.min, tzinfo=tz).astimezone(timezone.utc)


def parse_ical_events(
    ics: str | bytes,
    *,
    window_start: date | datetime | None = None,
    window_end: date | datetime | None = None,
    timezone_name: str = "Europe/London",
    fetched_at: datetime | None = None,
) -> list[CalendarEvent]:
    """Parse opaque, non-cancelled iCalendar events, expanding recurrences in a window."""
    if (window_start is None) != (window_end is None):
        raise ValueError("window_start and window_end must be supplied together")

    tz = ZoneInfo(timezone_name)
    calendar = Calendar.from_ical(ics)
    if window_start is None or window_end is None:
        components = calendar.walk("VEVENT")
    else:
        start = _window_datetime(window_start, tz, end=False)
        end = _window_datetime(window_end, tz, end=True)
        if end < start:
            raise ValueError("window_end must not precede window_start")
        components = recurring_ical_events.of(calendar).between(start, end)

    fetched_at_value = (
        fetched_at.astimezone(timezone.utc).isoformat()
        if fetched_at is not None
        else datetime.now(timezone.utc).isoformat()
    )
    events: list[CalendarEvent] = []
    for component in components:
        status = str(component.get("status", "CONFIRMED")).upper()
        transparency = str(component.get("transp", "OPAQUE")).upper()
        if status == "CANCELLED" or transparency == "TRANSPARENT":
            continue

        raw_start = component.decoded("dtstart")
        all_day = isinstance(raw_start, date) and not isinstance(raw_start, datetime)
        starts_at = _event_datetime(raw_start, tz)

        raw_end = component.decoded("dtend") if component.get("dtend") else None
        if raw_end is None:
            if all_day:
                date_end = raw_start + timedelta(days=1)
                end_value = date_end.isoformat()
                ends_at = _event_datetime(date_end, tz)
            else:
                ends_at = starts_at
                end_value = ends_at.isoformat()
        else:
            ends_at = _event_datetime(raw_end, tz)
            end_value = raw_end.isoformat() if all_day else ends_at.isoformat()
        if ends_at < starts_at:
            raise ValueError("VEVENT end precedes its start")

        uid = component.get("uid")
        if uid is None:
            raise ValueError("VEVENT is missing UID")
        external_id = f"{uid}@{starts_at.isoformat()}"
        events.append(
            CalendarEvent(
                external_id=external_id,
                title=str(component.get("summary")) if component.get("summary") else None,
                starts_at=raw_start.isoformat() if all_day else starts_at.isoformat(),
                ends_at=end_value,
                all_day=all_day,
                fetched_at=fetched_at_value,
            )
        )
    return sorted(events, key=lambda event: (event.starts_at, event.external_id))


def fetch_ical_events(
    ics_url: str,
    *,
    window_start: date | datetime,
    window_end: date | datetime,
    timezone_name: str = "Europe/London",
    fetched_at: datetime | None = None,
    client: httpx.Client | None = None,
) -> list[CalendarEvent]:
    """Fetch and parse the configured private iCalendar feed without logging its URL."""
    if client is not None:
        response = client.get(ics_url)
        response.raise_for_status()
        payload = response.content
    else:
        with httpx.Client(timeout=20.0) as session:
            response = session.get(ics_url)
            response.raise_for_status()
            payload = response.content
    return parse_ical_events(
        payload,
        window_start=window_start,
        window_end=window_end,
        timezone_name=timezone_name,
        fetched_at=fetched_at,
    )


def busy_slots(
    events: list[CalendarEvent],
    *,
    start: str | datetime,
    end: str | datetime,
) -> list[tuple[datetime, datetime]]:
    """Return timed event ranges that overlap the requested window."""
    window_start = datetime.fromisoformat(start) if isinstance(start, str) else start
    window_end = datetime.fromisoformat(end) if isinstance(end, str) else end
    if window_end < window_start:
        raise ValueError("end must not precede start")

    slots: list[tuple[datetime, datetime]] = []
    for event in events:
        if event.all_day:
            continue
        event_start = datetime.fromisoformat(event.starts_at)
        event_end = datetime.fromisoformat(event.ends_at)
        if event_start < window_end and event_end > window_start:
            slots.append((event_start, event_end))
    return sorted(slots)
