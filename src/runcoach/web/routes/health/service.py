from __future__ import annotations

from runcoach import db
from runcoach.web.routes.health.model import HealthStatus


class HealthService:
    @staticmethod
    def get_status() -> HealthStatus:
        """Report liveness with the database's applied migration number."""
        conn = db.connect()
        try:
            return HealthStatus(
                status="ok", db_version=db.schema_version(conn), app_version="0.1.0"
            )
        finally:
            conn.close()
