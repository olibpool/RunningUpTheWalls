from __future__ import annotations

from runcoach.domain.models import Activity


class ActivityUploadResponse(Activity):
    """Parsed activity export used by the upload endpoint."""
