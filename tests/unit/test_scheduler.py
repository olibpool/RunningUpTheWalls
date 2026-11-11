from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from runcoach.config import load_config, load_scheduling_config
from runcoach.domain.models import PlannedSession
from runcoach.engine.scheduler import Busy, FixedInstance, schedule_week


def _session(day: date, kind: str, minutes: int) -> PlannedSession:
    return PlannedSession(
        date=day,
        sport="run",
        kind=kind,
        title=f"{kind.title()} run",
        duration_min=minutes,
        rpe=6.0 if kind == "tempo" else 4.0,
    )


def _config(tmp_path, *, unavailable: bool = False):
    config = load_scheduling_config(load_config(local_path=tmp_path / "local.toml", env={}))
    if unavailable:
        availability = config.availability.model_copy(update={"weekday": [], "weekend": []})
        return config.model_copy(update={"availability": availability})
    return config


def _example_sessions() -> list[PlannedSession]:
    return [
        _session(date(2026, 11, 9), "easy", 30),
        _session(date(2026, 11, 12), "tempo", 45),
        _session(date(2026, 11, 14), "easy", 30),
        _session(date(2026, 11, 15), "long", 120),
    ]


def _football() -> list[FixedInstance]:
    return [
        FixedInstance(
            date=date(2026, 11, 10),
            title="Football",
            sport="football",
            start_time="19:00",
            duration_min=90,
            rpe=6.0,
        )
    ]


def test_scheduler_matches_example_week_and_keeps_rest_day(tmp_path) -> None:
    result = schedule_week(
        date(2026, 11, 9),
        _example_sessions(),
        2,
        _football(),
        [],
        [],
        _config(tmp_path),
    )

    by_kind = {
        placement.session.kind: placement
        for placement in result.placed
        if placement.session is not None
    }
    assert by_kind["climb_power"].date == date(2026, 11, 11)
    assert by_kind["tempo"].date == date(2026, 11, 12)
    assert by_kind["climb_volume"].date == date(2026, 11, 13)
    assert by_kind["long"].date == date(2026, 11, 15)
    assert len({placement.date for placement in result.placed}) < 7
    assert not result.dropped
    assert not result.warnings
    assert all(placement.reason for placement in result.placed)


def test_busy_thursday_moves_quality_and_explains_why(tmp_path) -> None:
    busy = Busy(
        start=datetime(2026, 11, 12, 17, 30, tzinfo=timezone.utc),
        end=datetime(2026, 11, 12, 21, 0, tzinfo=timezone.utc),
        title="Work event",
    )

    result = schedule_week(
        date(2026, 11, 9),
        _example_sessions(),
        2,
        _football(),
        [busy],
        [],
        _config(tmp_path),
    )

    quality = next(
        placement
        for placement in result.placed
        if placement.session is not None and placement.session.kind == "tempo"
    )
    assert quality.date == date(2026, 11, 13)
    assert "Work event" in quality.reason


def test_impossible_week_drops_easy_first_and_never_drops_long(tmp_path) -> None:
    result = schedule_week(
        date(2026, 11, 9),
        _example_sessions(),
        2,
        _football(),
        [],
        [],
        _config(tmp_path, unavailable=True),
    )

    assert result.dropped
    assert result.dropped[0].session.kind == "easy"
    assert all(item.session.kind != "long" for item in result.dropped)
    assert any("Long run could not be placed" in warning for warning in result.warnings)


def test_scheduler_relaxes_hard_spacing_to_24_hours_once(tmp_path) -> None:
    config = _config(tmp_path)
    availability = config.availability.model_copy(update={"weekday": [("06:30", "08:00")]})
    config = config.model_copy(update={"availability": availability})
    busy = [
        Busy(
            start=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
            + timedelta(hours=6),
            end=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
            + timedelta(hours=8, minutes=30),
            title="Busy morning",
        )
        for day in (date(2026, 11, 9), date(2026, 11, 11), date(2026, 11, 13))
    ]

    result = schedule_week(
        date(2026, 11, 9),
        [
            _session(date(2026, 11, 12), "tempo", 45),
            _session(date(2026, 11, 15), "long", 120),
        ],
        0,
        _football(),
        busy,
        [],
        config,
    )

    tempo = next(item for item in result.placed if item.session and item.session.kind == "tempo")
    assert result.relaxed
    assert tempo.date == date(2026, 11, 12)
    assert any("relaxed to 24 hours" in warning for warning in result.warnings)


def test_prior_tail_start_time_preserves_hard_session_spacing(tmp_path) -> None:
    prior = _session(date(2026, 11, 8), "tempo", 45).model_copy(update={"start_time": "20:00"})

    result = schedule_week(
        date(2026, 11, 9),
        [_session(date(2026, 11, 9), "tempo", 30)],
        0,
        [],
        [],
        [prior],
        _config(tmp_path),
    )

    tempo = result.placed[0]
    assert tempo.date == date(2026, 11, 10)
    assert tempo.start_time == "20:00"
    assert tempo.session is not None
    assert tempo.session.start_time == "20:00"


def test_fixed_commitment_conflict_is_reported_without_moving_it(tmp_path) -> None:
    fixed = _football()
    busy = Busy(
        start=datetime(2026, 11, 10, 19, 0, tzinfo=timezone.utc),
        end=datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc),
        title="Work event",
    )

    result = schedule_week(
        date(2026, 11, 9),
        [],
        0,
        fixed,
        [busy],
        [],
        _config(tmp_path),
    )

    assert result.placed[0].date == date(2026, 11, 10)
    assert any("conflicts with busy event Work event" in warning for warning in result.warnings)
