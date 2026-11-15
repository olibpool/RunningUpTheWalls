from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from icalendar import Calendar

from runcoach.config import load_config, load_scheduling_config
from runcoach.domain.models import Session, Step
from runcoach.integrations.watch import NullExporter, RepeatGroup, exporter_for, to_workout_spec
from runcoach.web.app import create_app

CFG = load_config(local_path=Path("does-not-exist.toml"), env={})


def _intervals() -> Session:
    return Session(
        id=7, plan_id=1, date=date(2027, 1, 6), start_time="18:00", sport="run",
        kind="intervals", title="Intervals", duration_min=44,
        steps=[
            Step(kind="warmup", duration_s=600, pace_min_s_per_km=300, pace_max_s_per_km=340),
            Step(kind="repeat", repeats=4, children=[
                Step(kind="work", duration_s=180, pace_min_s_per_km=236, pace_max_s_per_km=236),
                Step(kind="recover", duration_s=180, pace_min_s_per_km=300, pace_max_s_per_km=340),
            ]),
            Step(kind="cooldown", duration_s=600),
        ],
    )  # fmt: skip


def test_workout_spec_keeps_structure_paces_and_a_compact_name() -> None:
    spec = to_workout_spec(_intervals(), week_number=3)
    assert spec.name == "RC W3 Intervals"
    warmup, repeat, cooldown = spec.steps
    assert isinstance(repeat, RepeatGroup) and repeat.iterations == 4
    work = repeat.steps[0]
    assert (work.kind, work.end, work.value, work.pace_fast_s_per_km) == (
        "interval",
        "time",
        180.0,
        236,
    )
    assert not isinstance(cooldown, RepeatGroup) and cooldown.pace_fast_s_per_km is None
    with pytest.raises(ValueError):
        to_workout_spec(_intervals().model_copy(update={"sport": "climb"}), 1)


def test_exporter_selection_defaults_to_null() -> None:
    export = load_scheduling_config(CFG).export
    assert isinstance(exporter_for(export), NullExporter)
    with pytest.raises(NotImplementedError, match="P4-T1"):
        exporter_for(export.model_copy(update={"backend": "garmin"}))


def test_plan_ics_has_stable_uids_steps_and_a_timezone(tmp_path: Path) -> None:
    client = TestClient(create_app(db_path=tmp_path / "e.db", cfg=CFG))
    goal = (date.today() + timedelta(weeks=10)).isoformat()
    form = {
        "distance": "10k", "goal_date": goal, "vdot": "45", "current_weekly_km": "25",
        "runs_per_week": "3", "climbing_target_per_week": "0",
    }  # fmt: skip
    client.post("/plan/save", data=form)

    first = client.get("/export/plan.ics")
    second = client.get("/export/plan.ics")
    assert first.headers["content-type"].startswith("text/calendar")
    calendar = Calendar.from_ical(first.content)
    uids = [str(e["uid"]) for e in calendar.walk("VEVENT")]
    assert uids and all(u.startswith("session-") and u.endswith("@runcoach") for u in uids)
    assert uids == [str(e["uid"]) for e in Calendar.from_ical(second.content).walk("VEVENT")]
    assert calendar.walk("VTIMEZONE")
    assert any("/km" in str(e["description"]) for e in calendar.walk("VEVENT"))
