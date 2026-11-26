"""Open-Meteo hourly forecast (free, no API key)."""

from __future__ import annotations

import httpx

from runcoach.config import WeatherConfig

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def fetch_forecast(cfg: WeatherConfig, client: httpx.Client | None = None) -> dict[str, float]:
    """Return local 'YYYY-MM-DDTHH:00' -> apparent temperature for the next 7 days."""
    params: dict[str, str | int | float] = {
        "latitude": cfg.latitude,
        "longitude": cfg.longitude,
        "hourly": "temperature_2m,apparent_temperature,dew_point_2m",
        "timezone": "Europe/London",
        "forecast_days": 7,
    }
    if client is None:
        with httpx.Client(timeout=cfg.timeout_s) as own:
            response = own.get(FORECAST_URL, params=params)
    else:
        response = client.get(FORECAST_URL, params=params)
    response.raise_for_status()
    hourly = response.json()["hourly"]
    return {
        time: float(temp)
        for time, temp in zip(hourly["time"], hourly["apparent_temperature"])
        if temp is not None
    }
