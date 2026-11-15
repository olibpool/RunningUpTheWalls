from __future__ import annotations

from datetime import date

from runcoach.domain.models import Activity, Session
from runcoach.web.stats import completion, km_bars, load_ratio, recent, week_progress, weekly

MONDAY = date(2026, 11, 9)


def _run(day: str, km: float, load: float = 100, sport: str = "run") -> Activity:
    return Activity(
        source="manual", sport=sport, started_at=f"{day}T08:00:00+00:00",
        duration_s=3600, distance_m=km * 1000, load=load,
    )  # fmt: skip


def _session(day: date, status: str, km: float = 5) -> Session:
    return Session(
        id=1, plan_id=1, date=day, sport="run", kind="easy", title="Easy",
        distance_m=int(km * 1000), status=status,  # type: ignore[arg-type]
    )  # fmt: skip


def test_weekly_buckets_runs_by_local_monday_and_counts_load_for_every_sport() -> None:
    activities = [
        _run("2026-11-09", 10), _run("2026-11-15", 5),  # this week, Monday and Sunday
        _run("2026-11-02", 8), _run("2026-11-03", 0, load=300, sport="climb"),
        _run("2026-08-01", 50),  # too old to show
    ]  # fmt: skip
    weeks = weekly(activities, MONDAY, 3)
    assert [w.start for w in weeks] == [date(2026, 10, 26), date(2026, 11, 2), MONDAY]
    assert [w.run_km for w in weeks] == [0, 8, 15]
    assert [w.load for w in weeks] == [0, 400, 200]


def test_load_ratio_ignores_the_unfinished_current_week() -> None:
    weeks = weekly([_run("2026-11-02", 5, load=200), _run("2026-11-09", 5, load=900)], MONDAY, 3)
    assert load_ratio(weeks, chronic_weeks=4) is None  # no chronic history before last week
    weeks = weekly([_run("2026-10-26", 5, load=100), _run("2026-11-02", 5, load=150)], MONDAY, 3)
    assert load_ratio(weeks, chronic_weeks=4) == 1.5


def test_recent_completion_and_week_progress() -> None:
    today = date(2026, 11, 15)
    totals = recent(
        [_run("2026-11-15", 10), _run("2026-10-17", 6), _run("2026-10-16", 9)], today, 30
    )
    assert (totals.run_km, totals.runs, totals.hours) == (16, 2, 2)

    sessions = [
        _session(date(2026, 11, 10), "done"), _session(date(2026, 11, 11), "skipped"),
        _session(date(2026, 11, 12), "dropped"), _session(date(2026, 11, 14), "planned"),
        _session(today, "planned"),
    ]  # fmt: skip
    assert completion(sessions, today) == (1, 3)  # dropped and today's don't count
    progress = week_progress(sessions, [_run("2026-11-10", 6.2)])
    assert (progress.done, progress.total, progress.km_done, progress.km_planned) == (1, 4, 6.2, 15)
    assert progress.share == 0.25


def test_km_bars_mark_and_link_the_current_week() -> None:
    bars = km_bars(weekly([_run("2026-11-09", 7)], MONDAY, 2), MONDAY, link=True)
    assert [(b.current, b.value) for b in bars] == [(False, 0), (True, 7)]
    assert bars[1].href == "/week?start=2026-11-09" and "7.0 km" in bars[1].title
