from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile

from runcoach.web.routes.activity.model import ActivityUploadResponse
from runcoach.web.routes.activity.service import ActivityService

router = APIRouter(prefix="/activities", tags=["activities"])


@router.post("/upload", response_model=ActivityUploadResponse)
async def upload_activity(file: Annotated[UploadFile, File(...)]) -> ActivityUploadResponse:
    """Import a GPX activity file and return a parsed summary."""
    if file.filename is None or not file.filename.lower().endswith(".gpx"):
        raise HTTPException(status_code=400, detail="Only GPX files are supported right now")

    try:
        activity = ActivityService.import_activity(await file.read(), filename=file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ActivityUploadResponse.model_validate(activity.model_dump())
