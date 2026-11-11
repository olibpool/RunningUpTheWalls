from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from runcoach.config import ConfigNode, SchedulingConfig, load_scheduling_config


def _utc_datetime(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def should_replan(
    today: date,
    now_utc: datetime,
    last_replanned_at: str | None,
    cfg: ConfigNode | SchedulingConfig,
) -> bool:
    typed_cfg = cfg if isinstance(cfg, SchedulingConfig) else load_scheduling_config(cfg)
    if typed_cfg.continuous.replan_interval_days <= 0:
        raise ValueError("continuous.replan_interval_days must be positive")
    current_time = _utc_datetime(now_utc, "now_utc")
    last_replanned = None
    if last_replanned_at is not None:
        try:
            parsed = datetime.fromisoformat(last_replanned_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("last_replanned_at must be an ISO 8601 UTC timestamp") from exc
        last_replanned = _utc_datetime(parsed, "last_replanned_at")
    if today.weekday() == 0 or last_replanned is None:
        return True
    return current_time - last_replanned >= timedelta(
        days=typed_cfg.continuous.replan_interval_days
    )
