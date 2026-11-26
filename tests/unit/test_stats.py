from __future__ import annotations

from datetime import date

from runcoach.domain.models import Activity, Session
from runcoach.web.stats import completion, km_bars, recent, week_progress, weekly

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


def test_balance_orders_sports_and_folds_unknown_ones_into_other() -> None:
    from runcoach.web.stats import balance

    parts = balance(
        [("climb", 40.4), ("run", 100), ("tennis", 10)], [("run", 50), ("football", 300)]
    )
    assert [(p.sport, p.done, p.ahead) for p in parts] == [
        ("run", 100, 50), ("football", 0, 300), ("climb", 40, 0), ("other", 10, 0),
    ]  # fmt: skip


def test_streak_counts_full_weeks_and_a_filling_week_never_breaks_it() -> None:
    from runcoach.web.stats import streak

    weeks = ["2026-10-26", "2026-11-02"]  # two full weeks of three sessions
    activities = [_run(day, 5) for day in weeks for _ in range(3)]
    assert streak(activities, date(2026, 11, 10), 3) == 2  # this week has none yet
    activities += [_run("2026-11-09", 5)] * 3
    assert streak(activities, date(2026, 11, 10), 3) == 3
    assert streak([_run("2026-10-26", 5)] * 3, date(2026, 11, 10), 3) == 0  # a gap breaks it


def test_activity_grid_shades_days_and_records_find_bests() -> None:
    from runcoach.web.stats import activity_grid, records

    activities = [_run("2026-11-09", 10, load=400), _run("2026-11-10", 0, 100, "climb")]
    grid = activity_grid(activities, date(2026, 11, 11), 2)
    assert len(grid) == 2 and all(len(row) == 7 for row in grid)
    monday, tuesday, wednesday = grid[1][:3]
    assert (monday.sport, monday.level) == ("run", 4) and (tuesday.sport, tuesday.level) == (
        "climb",
        1,
    )
    assert wednesday.level == 0 and "rest" in wednesday.title

    fast = Activity(source="garmin", sport="run", started_at="2026-11-01T08:00:00+00:00",
                    duration_s=1500, distance_m=5000, load=150)  # fmt: skip
    best = records([*activities, fast, _run("2026-11-03", 3)], min_km=5)
    assert best.longest_run is activities[0] and best.fastest_run is fast
    assert best.biggest_week == (date(2026, 11, 9), 10.0)
    assert best.counts == {"run": 3, "climb": 1}


def test_reasons_read_like_a_coach_and_keep_the_phase_apart() -> None:
    from runcoach.display import phase, why

    reason = (
        "Build block; run volume reduced to 82% to respect the combined leg-load ceiling.; "
        "Thursday availability; spaced from football; something new"
    )
    assert why(reason) == [
        "Trimmed to 82% so running, football and climbing together stay within this week's load.",
        "Fits your Thursday time window.",
        "Kept apart from football so your legs recover between them.",
        "Something new.",
    ]
    assert phase(reason) == "Build" and phase("Aerobic emphasis.") == "Aerobic"
    assert why("Penalty Drinks (severity 2)") == ["Drinks: an easy day, nothing hard."]
    assert why(None) == [] and phase("") is None
