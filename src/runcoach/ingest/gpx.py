from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from runcoach.domain.models import Activity

_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


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


def parse_gpx_file(file_bytes: bytes, *, source: str = "gpx") -> Activity:
    """Parse a GPX track file into a structured activity summary."""
    try:
        root = ET.fromstring(file_bytes)
    except ET.ParseError as exc:
        raise ValueError("Invalid GPX file") from exc

    points: list[tuple[datetime, float, float]] = []
    for point in root.findall(".//gpx:trkpt", _NS):
        raw_time = point.findtext("gpx:time", default="", namespaces=_NS)
        if not raw_time:
            continue
        try:
            timestamp = _parse_iso8601(raw_time)
        except ValueError as exc:
            raise ValueError(f"Unsupported GPX timestamp: {raw_time!r}") from exc
        lat = float(point.attrib.get("lat", "0"))
        lon = float(point.attrib.get("lon", "0"))
        points.append((timestamp, lat, lon))

    if not points:
        raise ValueError("GPX file contains no track points")

    started_at = points[0][0]
    finished_at = points[-1][0]
    duration_s = max(int((finished_at - started_at).total_seconds()), 0)

    distance_m = 0.0
    for index in range(1, len(points)):
        previous_time, previous_lat, previous_lon = points[index - 1]
        current_time, current_lat, current_lon = points[index]
        if current_time <= previous_time:
            continue
        distance_m += _haversine_meters(previous_lat, previous_lon, current_lat, current_lon)

    sport = "run"
    track_type = root.findtext(".//gpx:type", default="", namespaces=_NS)
    if track_type:
        sport = track_type.lower()

    return Activity(
        source=source,
        external_id=None,
        sport=sport,
        started_at=started_at.astimezone(timezone.utc).isoformat(),
        duration_s=duration_s,
        distance_m=round(distance_m, 2),
        avg_hr=None,
        rpe=None,
        load=None,
        session_id=None,
        raw_path=None,
    )
