from __future__ import annotations

from runcoach.ingest.gpx import parse_gpx_file

SAMPLE_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
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
"""


def test_parse_gpx_file_extracts_activity_summary() -> None:
    summary = parse_gpx_file(SAMPLE_GPX)

    assert summary.source == "gpx"
    assert summary.sport == "run"
    assert summary.started_at == "2026-11-02T06:30:00+00:00"
    assert summary.duration_s == 360
    assert summary.distance_m > 0.0


def test_gpx_1_0_type_mapping_and_idempotent_id() -> None:
    gpx_10 = SAMPLE_GPX.replace(b"GPX/1/1", b"GPX/1/0").replace(
        b"<name>Morning Run</name>", b"<name>Climb</name><type>bouldering</type>"
    )
    summary = parse_gpx_file(gpx_10)
    assert summary.sport == "climb"
    assert summary.duration_s == 360
    assert summary.external_id == parse_gpx_file(gpx_10).external_id
    assert summary.external_id != parse_gpx_file(SAMPLE_GPX).external_id


def test_gpx_rejects_files_that_are_not_gpx() -> None:
    import pytest

    with pytest.raises(ValueError, match="Not a GPX"):
        parse_gpx_file(b"<kml xmlns='http://www.opengis.net/kml/2.2'/>")
