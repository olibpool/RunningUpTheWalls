from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile

from runcoach.domain.models import Activity
from runcoach.ingest.gpx import parse_gpx_file

# shortcut: JSON parse-only endpoint kept from the earlier build; P3-T4 replaces it with the
# HTML upload that stores and matches activities.
router = APIRouter(prefix="/activities", tags=["activities"])


@router.post("/upload")
async def upload_activity(file: Annotated[UploadFile, File(...)]) -> Activity:
    """Parse an uploaded GPX file and return its summary."""
    if file.filename is None or not file.filename.lower().endswith(".gpx"):
        raise HTTPException(status_code=400, detail="Only GPX files are supported right now")
    try:
        return parse_gpx_file(await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
