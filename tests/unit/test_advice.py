from __future__ import annotations

from datetime import date, timedelta

from configs import typed

from runcoach.domain.models import PlannedSession
from runcoach.engine.advice import session_notes

CFG = typed()
RACE_DAY = date(2027, 3, 14)


def _run(kind: str, minutes: int, day: date = date(2027, 2, 1)) -> PlannedSession:
    return PlannedSession(date=day, sport="run", kind=kind, title=kind, duration_min=minutes)


def test_long_runs_get_fuelling_and_short_runs_get_nothing() -> None:
    assert "g of carbohydrate an hour" in " ".join(session_notes(_run("long", 90), None, CFG))
    assert session_notes(_run("easy", 40), None, CFG) == []
    climb = PlannedSession(date=RACE_DAY, sport="climb", kind="climb_power", title="Climb")
    assert session_notes(climb, (RACE_DAY, "marathon"), CFG) == []


def test_carb_loading_before_half_and_marathon_only() -> None:
    def loads(distance: str, days_before: int) -> bool:
        session = _run("easy", 20, RACE_DAY - timedelta(days=days_before))
        return any("Carb-load" in n for n in session_notes(session, (RACE_DAY, distance), CFG))  # type: ignore[arg-type]

    assert loads("half", 1) and not loads("half", 2)
    assert loads("marathon", 1) and loads("marathon", 2) and not loads("marathon", 3)
    assert not loads("10k", 1) and not loads("5k", 1)


def test_race_day_pacing_by_distance_and_heat() -> None:
    five = session_notes(_run("race", 25, RACE_DAY), (RACE_DAY, "5k"), CFG)
    assert "first kilometre at target pace" in five[0] and len(five) == 1
    marathon = " ".join(session_notes(_run("race", 210, RACE_DAY), (RACE_DAY, "marathon"), CFG))
    assert "slower than target" in marathon and "breakfast" in marathon and "During" in marathon
    hot = session_notes(_run("easy", 40), None, CFG, heat=True)
    assert len(hot) == 1 and "electrolytes" in hot[0]
