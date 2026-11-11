from __future__ import annotations

from pydantic import BaseModel, Field


class HealthStatus(BaseModel):
    status: str = Field(default="ok")
    db_version: int = Field(default=1)
    app_version: str = Field(default="0.1.0")
