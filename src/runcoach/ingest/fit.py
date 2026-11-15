"""Parse FIT activity files (watch uploads and Garmin Connect originals) with fitparse."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import datetime, timezone
from typing import Any

from fitparse import FitFile, FitParseError

from runcoach.domain.models import Activity, Lap

# FIT sport values (and climbing sub-sports) to our sports; anything else is "other".
_SPORTS = {
    "running": "run",
    "rock_climbing": "climb",
    "soccer": "football",
}
_CLIMBING_SUB_SPORTS = {"indoor_climbing", "bouldering"}


def _sport(sport: object, sub_sport: object) -> str:
    if sub_sport in _CLIMBING_SUB_SPORTS:
        return "climb"
    return _SPORTS.get(str(sport), "other")


def _utc(moment: datetime) -> str:
    return moment.replace(tzinfo=timezone.utc).isoformat()  # fitparse gives naive UTC


def _speed(values: dict[str, Any]) -> float | None:
    speed = values.get("enhanced_avg_speed") or values.get("avg_speed")
    return float(speed) if speed else None


def unzip_fit(data: bytes) -> bytes:
    """Garmin Connect serves originals as a zip holding one .fit; plain FIT passes through."""
    if not zipfile.is_zipfile(io.BytesIO(data)):
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".fit")]
        if not names:
            raise ValueError("The zip holds no .fit file")
        return archive.read(names[0])


def parse_fit_file(file_bytes: bytes, *, source: str = "fit") -> Activity:
    """Parse a FIT activity: sport, start, timer duration, distance, heart rate and laps."""
    data = unzip_fit(file_bytes)
    try:
        fit = FitFile(io.BytesIO(data))
        sessions = [m.get_values() for m in fit.get_messages("session")]
        laps = [m.get_values() for m in fit.get_messages("lap")]
    except FitParseError as exc:
        raise ValueError(f"Invalid FIT file: {exc}") from exc
    if not sessions:
        raise ValueError("FIT file has no session (is it an activity file?)")
    first = sessions[0]
    start = first.get("start_time")
    if not isinstance(start, datetime):
        raise ValueError("FIT session has no start time")
    duration = sum(float(s.get("total_timer_time") or 0) for s in sessions)
    distance = sum(float(s.get("total_distance") or 0) for s in sessions)
    hr = first.get("avg_heart_rate")
    return Activity(
        source=source,
        external_id=hashlib.sha256(data).hexdigest(),
        sport=_sport(first.get("sport"), first.get("sub_sport")),
        started_at=_utc(start),
        duration_s=round(duration),
        distance_m=round(distance, 2) or None,
        avg_hr=int(hr) if hr else None,
        laps=[
            Lap(
                duration_s=float(lap.get("total_timer_time") or 0),
                distance_m=lap.get("total_distance"),
                avg_speed_mps=_speed(lap),
                avg_hr=lap.get("avg_heart_rate"),
                step_index=lap.get("wkt_step_index"),
            )
            for lap in laps
        ],
    )
