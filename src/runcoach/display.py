"""Text formatting shared by the CLI and the web pages."""

from __future__ import annotations

from datetime import date, timedelta

from runcoach.domain.models import Step


def pace(seconds_per_km: float) -> str:
    """Format a pace in s/km as m:ss."""
    return f"{int(seconds_per_km // 60)}:{int(seconds_per_km % 60):02d}"


def step_pace(step: Step) -> str:
    """Pace target of one step, e.g. '5:01-5:39/km', or '' when it has none."""
    if not (step.pace_min_s_per_km and step.pace_max_s_per_km):
        return ""
    low, high = pace(step.pace_min_s_per_km), pace(step.pace_max_s_per_km)
    return f"{low}/km" if low == high else f"{low}-{high}/km"


def main_pace(steps: list[Step]) -> str:
    """Pace of the first work or steady step, searching inside repeats."""
    for step in steps:
        if step.kind == "repeat":
            found = main_pace(step.children)
            if found:
                return found
        elif step.kind in {"work", "steady"} and step_pace(step):
            return step_pace(step)
    return ""


def clock(seconds: int) -> str:
    """Format a duration in seconds as m:ss, or h:mm:ss from an hour up."""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def structure(steps: list[Step]) -> str:
    """Describe the main set from the steps, which stay correct after load scaling."""
    for step in steps:
        if step.kind == "repeat" and step.children and step.children[0].duration_s:
            return f"{step.repeats} x {clock(step.children[0].duration_s)}"
        if step.kind == "work" and step.duration_s:
            return f"{round(step.duration_s / 60)} min"
    return ""


def km(distance_m: float | None) -> str:
    """Format metres as kilometres to one decimal, or '' when unknown."""
    return f"{distance_m / 1000:.1f} km" if distance_m else ""


def monday(day: date) -> date:
    """Return the Monday of the week containing day."""
    return day - timedelta(days=day.weekday())
