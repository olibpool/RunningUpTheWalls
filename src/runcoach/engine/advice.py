"""Fuelling and pacing notes for a session. Pure: the caller supplies the race, if any."""

from __future__ import annotations

from datetime import date

from runcoach.config import SchedulingConfig
from runcoach.domain.models import Distance, PlannedSession

_LONG_RACES: set[Distance] = {"half", "marathon"}


def session_notes(
    session: PlannedSession,
    race: tuple[date, Distance] | None,
    cfg: SchedulingConfig,
    *,
    heat: bool = False,
) -> list[str]:
    """Practical fuelling, carb-loading, pacing and heat notes for one session."""
    if session.sport != "run":
        return []
    fuel = cfg.fuel
    low, high = fuel.carbs_g_per_h
    minutes = session.duration_min or 0
    notes: list[str] = []
    if session.kind == "race" and race is not None:
        notes += _race_notes(race[1], minutes, cfg)
    elif minutes >= fuel.long_run_min:
        notes.append(
            f"Over {fuel.long_run_min} minutes: take {low}-{high} g of carbohydrate an hour from "
            "about 45 minutes in (gels, sports drink or chews), with water. Practise what you "
            "will use on race day."
        )
    if race is not None and race[1] in fuel.carb_load_days:
        days_before = (race[0] - session.date).days
        if 1 <= days_before <= fuel.carb_load_days[race[1]]:
            g_low, g_high = fuel.carb_load_g_per_kg
            notes.append(
                f"Carb-load today: about {g_low}-{g_high} g of carbohydrate per kg of body "
                "weight, from familiar low-fibre foods such as rice, pasta, bread and "
                "potatoes. Keep fat and fibre low."
            )
    if heat:
        notes.append(
            "Hot day: drink to thirst before and during, and add electrolytes on runs over an hour."
        )
    return notes


def _race_notes(distance: Distance, minutes: int, cfg: SchedulingConfig) -> list[str]:
    fuel = cfg.fuel
    slow_low, slow_high = fuel.first_km_slower_s
    if distance == "5k":
        notes = [
            "Pacing: run the first kilometre at target pace, not faster; adrenaline makes "
            "10 s/km too fast feel easy. Hold it, then finish hard over the last kilometre."
        ]
    else:
        notes = [
            f"Pacing: start {slow_low}-{slow_high} s/km slower than target for the first "
            "kilometre, settle onto target, and aim for an even or slightly faster second half."
        ]
    if distance in _LONG_RACES:
        notes.append("Before: a carbohydrate-rich breakfast you have tried, 2-4 hours before.")
        if minutes >= fuel.long_run_min:
            low, high = fuel.carbs_g_per_h
            notes.append(
                f"During: {low}-{high} g of carbohydrate an hour from about 30-45 minutes in, "
                "and drink to thirst. Use only what you practised in long runs."
            )
    return notes
