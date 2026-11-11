from __future__ import annotations

from runcoach.web.routes.health.model import HealthStatus


class HealthService:
    @staticmethod
    def get_status() -> HealthStatus:
        return HealthStatus(status="ok", db_version=1, app_version="0.1.0")
