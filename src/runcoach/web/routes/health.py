from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends

from runcoach import db
from runcoach.web.deps import get_conn

router = APIRouter()
APP_VERSION = "0.1.0"


@router.get("/healthz")
def healthz(conn: Annotated[sqlite3.Connection, Depends(get_conn)]) -> dict[str, object]:
    """Liveness check that also proves the database opens and is migrated."""
    return {"status": "ok", "db_version": db.schema_version(conn), "app_version": APP_VERSION}
