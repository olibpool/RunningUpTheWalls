from __future__ import annotations

from runcoach.domain.models import Activity
from runcoach.ingest.gpx import parse_gpx_file


class ActivityService:
    @staticmethod
    def import_activity(file_bytes: bytes, *, filename: str) -> Activity:
        """Import a workout file and return its parsed activity summary."""
        if not filename.lower().endswith(".gpx"):
            raise ValueError("Only GPX uploads are supported right now")
        return parse_gpx_file(file_bytes)
