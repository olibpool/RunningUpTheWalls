from __future__ import annotations

import hashlib
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from runcoach.domain.models import Activity

_GPX_NAMESPACES = {
    "http://www.topografix.com/GPX/1/1",
    "http://www.topografix.com/GPX/1/0",
}
# Track <type> values seen from Garmin, Strava and others; anything else is "other".
_SPORT_WORDS = (
    ("run", "run"),
    ("jog", "run"),
    ("climb", "climb"),
    ("boulder", "climb"),
    ("football", "football"),
    ("soccer", "football"),
)


def _parse_iso8601(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    return datetime.fromisoformat(normalized)


def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius_m * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _sport(track_type: str) -> str:
    lowered = track_type.lower()
    if not lowered:
        return "run"  # no type: assume a run; the upload form can correct it
    return next((sport for word, sport in _SPORT_WORDS if word in lowered), "other")


def parse_gpx_file(file_bytes: bytes, *, source: str = "gpx") -> Activity:
    """Parse a GPX 1.0 or 1.1 track into an activity; external_id is the file's SHA-256."""
    try:
        root = ET.fromstring(file_bytes)
    except ET.ParseError as exc:
        raise ValueError("Invalid GPX file") from exc
    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    if namespace not in _GPX_NAMESPACES:
        raise ValueError("Not a GPX 1.0 or 1.1 file")
    ns = {"gpx": namespace}

    points: list[tuple[datetime, float, float]] = []
    for point in root.findall(".//gpx:trkpt", ns):
        raw_time = point.findtext("gpx:time", default="", namespaces=ns)
        if not raw_time:
            continue
        try:
            timestamp = _parse_iso8601(raw_time)
        except ValueError as exc:
            raise ValueError(f"Unsupported GPX timestamp: {raw_time!r}") from exc
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)  # GPX times are UTC
        points.append((timestamp, float(point.attrib["lat"]), float(point.attrib["lon"])))

    if not points:
        raise ValueError("GPX file contains no timed track points")

    started_at = points[0][0]
    duration_s = max(int((points[-1][0] - started_at).total_seconds()), 0)
    distance_m = 0.0
    for (prev_time, prev_lat, prev_lon), (time_, lat, lon) in zip(points, points[1:]):
        if time_ > prev_time:
            distance_m += _haversine_meters(prev_lat, prev_lon, lat, lon)

    return Activity(
        source=source,
        external_id=hashlib.sha256(file_bytes).hexdigest(),
        sport=_sport(root.findtext(".//gpx:trk/gpx:type", default="", namespaces=ns)),
        started_at=started_at.astimezone(timezone.utc).isoformat(),
        duration_s=duration_s,
        distance_m=round(distance_m, 2),
    )
