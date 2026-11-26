"""Store activity files: one file at a time, or everything waiting in the inbox folder."""

from __future__ import annotations

import logging
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from runcoach import db
from runcoach.config import SchedulingConfig
from runcoach.domain.models import Activity
from runcoach.ingest.fit import parse_fit_file
from runcoach.ingest.gpx import parse_gpx_file

log = logging.getLogger(__name__)

# A ".zip" is a Garmin Connect "original" download, which wraps one FIT file.
PARSERS: dict[str, Callable[[bytes], Activity]] = {
    ".gpx": parse_gpx_file,
    ".fit": parse_fit_file,
    ".zip": parse_fit_file,
}


@dataclass(frozen=True)
class IngestResult:
    activity_id: int
    added: bool  # False when the same file was ingested before


@dataclass(frozen=True)
class InboxReport:
    added: int
    duplicates: int
    failed: int


def ingest_file(
    conn: sqlite3.Connection, data: bytes, filename: str, cfg: SchedulingConfig
) -> IngestResult:
    """Parse and store one activity file; re-ingesting the same bytes changes nothing."""
    parser = PARSERS.get(Path(filename).suffix.lower())
    if parser is None:
        raise ValueError(f"Unsupported file type: {filename} (supported: {', '.join(PARSERS)})")
    return store_activity(conn, parser(data), cfg)


def store_activity(
    conn: sqlite3.Connection, activity: Activity, cfg: SchedulingConfig
) -> IngestResult:
    """Store a parsed activity once: the same file, or the same run from another source,
    is not stored again."""
    existing = (
        db.find_activity(conn, activity.source, activity.external_id)
        if activity.external_id
        else None
    ) or db.activity_starting_at(conn, activity.started_at)
    if existing is not None:
        return IngestResult(existing, added=False)
    if activity.rpe is None and activity.load is None:
        # Until it is matched, estimate load from the sport's default RPE.
        defaults: dict[str, float] = {str(k): v for k, v in cfg.default_rpe.items()}
        default = defaults.get(activity.sport, defaults["other"])
        activity = activity.model_copy(update={"load": default * activity.duration_s / 60})
    return IngestResult(db.insert_activity(conn, activity), added=True)


def ingest_inbox(conn: sqlite3.Connection, cfg: SchedulingConfig) -> InboxReport:
    """Ingest every supported file in the inbox, moving each to processed/ or failed/."""
    inbox = Path(cfg.inbox.path).expanduser()
    if not inbox.is_dir():
        return InboxReport(0, 0, 0)
    added = duplicates = failed = 0
    for path in sorted(p for p in inbox.iterdir() if p.suffix.lower() in PARSERS):
        try:
            result = ingest_file(conn, path.read_bytes(), path.name, cfg)
        except ValueError as exc:
            log.warning("Could not ingest %s: %s", path.name, exc)
            destination, failed = "failed", failed + 1
        else:
            destination = "processed"
            added, duplicates = added + result.added, duplicates + (not result.added)
        (inbox / destination).mkdir(exist_ok=True)
        shutil.move(str(path), inbox / destination / path.name)
    return InboxReport(added, duplicates, failed)
