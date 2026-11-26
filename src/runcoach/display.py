"""Text formatting shared by the CLI and the web pages."""

from __future__ import annotations

import re
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


def describe_steps(steps: list[Step], indent: str = "") -> list[str]:
    """One line per step, e.g. 'Work 3:00 at 3:56/km', with repeats nested."""
    lines: list[str] = []
    for step in steps:
        if step.kind == "repeat":
            lines.append(f"{indent}{step.repeats} x")
            lines += describe_steps(step.children, indent + "  ")
            continue
        parts = [step.kind.title()]
        if step.duration_s:
            parts.append(clock(step.duration_s))
        if step.distance_m:
            parts.append(km(step.distance_m))
        if step_pace(step):
            parts.append(f"at {step_pace(step)}")
        elif step.rpe:
            parts.append(f"RPE {step.rpe:g}")
        lines.append(indent + " ".join(parts))
    return lines


# The planner's reasons are terse fragments joined by "; "; these say them the way a coach would.
_WHY = [
    (r"(\w+) block, cut-back week", "A lighter cut-back week, so your body absorbs the training."),
    (r"(\w+) (block|emphasis)", ""),  # the phase is shown on its own
    (
        r"run volume reduced to (\d+)% to respect the combined leg-load ceiling",
        r"Trimmed to \1% so running, football and climbing together stay within this week's load.",
    ),
    (r"(\w+day) availability", r"Fits your \1 time window."),
    (r"day after football", "Placed the day after football."),
    (r"spaced from football", "Kept apart from football so your legs recover between them."),
    (r"spaced before the long run", "Leaves your legs fresh for the long run."),
    (r"weekend long-run slot", "On the weekend, when there's time for the long run."),
    (r"Aerobic maintenance", "Easy miles that build your aerobic engine."),
    (r"Long aerobic session", "Your long run: the backbone of endurance."),
    (r"Penalty (.+) \(severity \d\)", r"\1: an easy day, nothing hard."),
    (r"Easy day after penalty (.+)", r"Easy day after \1."),
]


def why(reason: str | None) -> list[str]:
    """A session's reason as plain sentences, without the phase name."""
    out: list[str] = []
    for part in (reason or "").split(";"):
        part = part.strip().rstrip(".")
        if not part:
            continue
        for pattern, text in _WHY:
            if re.fullmatch(pattern, part, flags=re.IGNORECASE):
                part = re.sub(pattern, text, part, flags=re.IGNORECASE)
                break
        else:
            part = part[:1].upper() + part[1:] + "."
        if part and part not in out:
            out.append(part)
    return out


_PLAIN = [
    (r"Easy day after penalty (.+?)(?=[:;.]|$)", r"The day after \1"),
    (r"Penalty (.+?) \(severity \d\)", r"\1"),
    (r"Check-in reports ", "You reported "),
    (r"Blocked by (.+?)(?=[:;.]|$)", r"Away (\1)"),
]


def plain(reason: str | None) -> str:
    """An adjustment's reason with the planner's terms for events said plainly."""
    text = reason or ""
    for pattern, repl in _PLAIN:
        text = re.sub(pattern, repl, text)
    return text


def phase(reason: str | None) -> str | None:
    """The training phase named in a reason ("Base", "Build", "Aerobic"...), if any."""
    found = re.search(r"(\w+) (?:block|emphasis)", reason or "", flags=re.IGNORECASE)
    return found.group(1).title() if found else None
