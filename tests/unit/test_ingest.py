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


def test_fit_activity_summary_laps_and_garmin_zip() -> None:
    import io
    import zipfile
    from datetime import datetime, timezone

    from fitdata import activity_fit

    from runcoach.ingest.fit import parse_fit_file

    start = datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc)
    data = activity_fit(start, [(600, 1800), (180, 620)], step_indexes=[0, 1])
    activity = parse_fit_file(data)
    assert activity.sport == "run"
    assert activity.started_at == "2026-11-02T06:30:00+00:00"
    assert activity.duration_s == 780 and activity.distance_m == 2420
    assert activity.avg_hr == 150
    assert [lap.step_index for lap in activity.laps] == [0, 1]
    assert round(activity.laps[1].avg_speed_mps or 0, 2) == round(620 / 180, 2)

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("123_ACTIVITY.fit", data)
    assert parse_fit_file(archive.getvalue()).external_id == activity.external_id


def test_fit_sport_mapping_and_rejects_garbage() -> None:
    from datetime import datetime, timezone

    import pytest
    from fitdata import activity_fit

    from runcoach.ingest.fit import parse_fit_file

    start = datetime(2026, 11, 2, 18, tzinfo=timezone.utc)
    assert parse_fit_file(activity_fit(start, [(5400, 0)], sport="soccer")).sport == "football"
    climbing = activity_fit(start, [(3600, 0)], sport="rock_climbing")
    assert parse_fit_file(climbing).sport == "climb"
    with pytest.raises(ValueError):
        parse_fit_file(b"not a fit file at all")
