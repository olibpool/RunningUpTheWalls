from __future__ import annotations

from fastapi.testclient import TestClient

from runcoach.ingest.gpx import parse_gpx_file
from runcoach.web.app import app

SAMPLE_GPX = b'''<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="coachal" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
    <name>Morning Run</name>
    <trkseg>
      <trkpt lat="51.5074" lon="-0.1278">
        <time>2026-11-02T06:30:00Z</time>
      </trkpt>
      <trkpt lat="51.5082" lon="-0.1272">
        <time>2026-11-02T06:36:00Z</time>
      </trkpt>
    </trkseg>
  </trk>
</gpx>
'''


def test_parse_gpx_file_extracts_activity_summary() -> None:
    summary = parse_gpx_file(SAMPLE_GPX)

    assert summary.source == "gpx"
    assert summary.sport == "run"
    assert summary.started_at == "2026-11-02T06:30:00+00:00"
    assert summary.duration_s == 360
    assert summary.distance_m > 0.0


def test_upload_route_accepts_gpx_file() -> None:
    client = TestClient(app)

    response = client.post(
        "/activities/upload",
        files={"file": ("sample.gpx", SAMPLE_GPX, "application/gpx+xml")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "gpx"
    assert payload["sport"] == "run"
    assert payload["duration_s"] == 360
    assert payload["distance_m"] > 0.0
