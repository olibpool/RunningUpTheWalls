from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from runcoach import db
from runcoach.web.deps import get_conn

router = APIRouter()
APP_VERSION = "0.1.0"


@router.get("/healthz")
def healthz(conn: Annotated[sqlite3.Connection, Depends(get_conn)]) -> dict[str, object]:
    """Liveness check that also proves the database opens and is migrated."""
    return {"status": "ok", "db_version": db.schema_version(conn), "app_version": APP_VERSION}


_STATIC = Path(__file__).resolve().parents[1] / "static"


@router.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    """Served from the root so the worker's scope covers every page; never cached."""
    return FileResponse(
        _STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"}
    )


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> FileResponse:
    """The PWA manifest with its registered media type."""
    return FileResponse(_STATIC / "manifest.webmanifest", media_type="application/manifest+json")
