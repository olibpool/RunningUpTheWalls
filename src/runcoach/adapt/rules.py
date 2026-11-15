"""Rule-based adaptation. Pure: rules read a PlanState and return Changes; db.apply_changes
writes them with their adjustment-log rows. Rules run on every sync, so each one must be
idempotent: load-scaling rules skip sessions the log says they already adjusted."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from runcoach.config import SchedulingConfig
from runcoach.display import monday
from runcoach.domain.models import Activity, Checkin, PlannedSession, Session, Step
from runcoach.engine.events import DayConstraint
from runcoach.engine.load import session_load
from runcoach.engine.ramp import ramp_for_block
from runcoach.engine.templates import scale_session

_QUALITY = {"tempo", "intervals", "time_trial"}


@dataclass(frozen=True)
class Change:
    """One change to a session. `updates` uses PlannedSession field names plus `status`.

    action: modify | drop | add | flag (flag stores updates["value"] in kv under rule).
    """

    session_id: int | None
    action: str
    updates: dict[str, object] = field(default_factory=dict)
    rule: str = "unknown"
    reason: str = ""


@dataclass(frozen=True)
class PlanState:
    sessions: list[Session]
    activities: list[Activity]
    checkins: list[Checkin]
    applied: frozenset[tuple[int | None, str]] = frozenset()  # (session_id, rule) already logged
    flags: frozenset[str] = frozenset()  # kv keys already set
    # Day limits from events the plan was not generated with (drinks, holidays, active trips).
    constraints: dict[date, DayConstraint] = field(default_factory=dict)
    blocks: list[tuple[date, date]] = field(default_factory=list)  # holidays needing a ramp


def _session_fields(session: PlannedSession) -> dict[str, object]:
    return {
        "kind": session.kind,
        "title": session.title,
        "steps": session.steps,
        "duration_min": session.duration_min,
        "distance_m": session.distance_m,
        "rpe": session.rpe,
        "leg_load": session.leg_load,
    }


def _scale_week(
    state: PlanState, week: date, factor: float, rule: str, reason: str
) -> list[Change]:
    """Scale the planned runs of one week, skipping any this rule already adjusted."""
    runs = [
        s
        for s in state.sessions
        if monday(s.date) == week and s.status == "planned" and s.sport == "run"
    ]
    if any((s.id, rule) in state.applied for s in runs):
        return []
    return [
        Change(s.id, "modify", _session_fields(scale_session(s, factor)), rule, reason)
        for s in runs
    ]


def missed_once(state: PlanState, today: date) -> list[Change]:
    """A planned session whose day has passed unmatched is skipped, never crammed in later."""
    return [
        Change(
            s.id,
            "modify",
            {"status": "skipped"},
            "missed_once",
            f"Missed on {s.date.isoformat()}; skipped rather than moved into another day.",
        )
        for s in state.sessions
        if s.status == "planned" and s.date < today
    ]


def missed_two_plus(state: PlanState, today: date) -> list[Change]:
    """Two or more skipped sessions in a week cap next week's runs at that week's planned load."""
    newly_skipped = {c.session_id for c in missed_once(state, today)}
    skipped_by_week: dict[date, int] = {}
    for s in state.sessions:
        if s.status == "skipped" or s.id in newly_skipped:
            skipped_by_week[monday(s.date)] = skipped_by_week.get(monday(s.date), 0) + 1
    changes: list[Change] = []
    for week, count in sorted(skipped_by_week.items()):
        if count < 2:
            continue
        cap = sum(s.leg_load for s in state.sessions if monday(s.date) == week and s.sport == "run")
        following = week + timedelta(days=7)
        planned = sum(
            s.leg_load
            for s in state.sessions
            if monday(s.date) == following and s.status == "planned" and s.sport == "run"
        )
        if planned > cap > 0:
            changes += _scale_week(
                state,
                following,
                cap / planned,
                "missed_two_plus",
                f"{count} sessions missed in the week of {week.isoformat()}; "
                "this week repeats that week's planned volume instead of increasing it.",
            )
    return changes


def ill_or_niggle(state: PlanState, cfg: SchedulingConfig) -> list[Change]:
    """Illness or a niggle: run by feel and downgrade quality for a window, then ramp back."""
    changes: list[Change] = []
    for checkin in state.checkins:
        if not (checkin.ill or checkin.niggle):
            continue
        what = "illness" if checkin.ill else f"a niggle ({checkin.niggle})"
        days = cfg.illness.ill_days if checkin.ill else cfg.illness.niggle_days
        end = checkin.date + timedelta(days=days)
        for s in state.sessions:
            if not (
                checkin.date <= s.date < end
                and s.status == "planned"
                and s.sport == "run"
                and (s.id, "ill_or_niggle") not in state.applied
            ):
                continue
            kind = "easy" if s.kind in _QUALITY else s.kind
            rpe = cfg.rpe.easy if kind == "easy" else (s.rpe or cfg.rpe.easy)
            minutes = s.duration_min or 0
            by_feel = s.model_copy(
                update={
                    "kind": kind,
                    "title": f"{'Easy run' if kind != s.kind else s.title} (by feel)",
                    "steps": [Step(kind="steady", duration_s=minutes * 60, rpe=rpe)],
                    "rpe": rpe,
                    "leg_load": session_load("run", kind, rpe, minutes, cfg.load_factors).leg,
                }
            )
            changes.append(
                Change(
                    s.id,
                    "modify",
                    _session_fields(by_feel),
                    "ill_or_niggle",
                    f"Check-in on {checkin.date.isoformat()} reported {what}: no pace targets "
                    f"and no quality for {days} days.",
                )
            )
        first_week = monday(end + timedelta(days=6))  # first full week after the window
        for offset, factor in enumerate(cfg.illness.return_ramp):
            changes += _scale_week(
                state,
                first_week + timedelta(weeks=offset),
                factor,
                "illness_ramp",
                f"Returning after {what}: runs at {factor:.0%} of planned load.",
            )
    return changes


def _pace(seconds: float, metres: float) -> float:
    return seconds / (metres / 1000)


def retest_suggested(state: PlanState, cfg: SchedulingConfig) -> list[Change]:
    """Suggest a re-test when recent quality runs were all faster than target at no extra effort.

    VDOT is never changed here; the human confirms a time trial first.
    """
    if "retest_suggested" in state.flags:
        return []
    by_session = {a.session_id: a for a in state.activities if a.session_id is not None}
    quality = sorted(
        (s for s in state.sessions if s.status == "done" and s.kind in {"tempo", "intervals"}),
        key=lambda s: s.date,
    )[-cfg.retest.quality_runs :]
    if len(quality) < cfg.retest.quality_runs:
        return []
    for s in quality:
        activity = by_session.get(s.id)
        if not (
            activity
            and activity.distance_m
            and activity.rpe is not None
            and s.rpe is not None
            and s.distance_m
            and s.duration_min
        ):
            return []
        planned = _pace(s.duration_min * 60, s.distance_m)
        actual = _pace(activity.duration_s, activity.distance_m)
        if actual > planned * (1 - cfg.retest.faster_by) or activity.rpe > s.rpe:
            return []
    reason = (
        f"Your last {len(quality)} quality runs were all over "
        f"{cfg.retest.faster_by:.0%} faster than target at or below the planned effort. "
        "Consider a 5 km time trial to update your paces."
    )
    return [Change(None, "flag", {"value": reason}, "retest_suggested", reason)]


_HARD_KINDS = {"tempo", "intervals", "long", "time_trial", "climb_power"}


def _within_limit(session: Session, constraint: DayConstraint) -> bool:
    if constraint.max_intensity == "blocked":
        return constraint.travel_allowed and session.sport == "run" and session.kind == "easy"
    limited = constraint.max_intensity == "easy" and (
        constraint.easy_sports is None or session.sport in constraint.easy_sports
    )
    too_hard = constraint.max_rpe is not None and (session.rpe or 0) > constraint.max_rpe
    return not (limited and (session.kind in _HARD_KINDS or too_hard))


def event_constraints(state: PlanState, cfg: SchedulingConfig) -> list[Change]:
    """Events logged after the plan was made: drop sessions on blocked days, ease hard ones."""
    changes: list[Change] = []
    for s in state.sessions:
        constraint = state.constraints.get(s.date)
        if (
            constraint is None
            or s.status != "planned"
            or (s.id, "event_constraint") in state.applied
            or _within_limit(s, constraint)
        ):
            continue
        if constraint.max_intensity == "blocked":
            changes.append(
                Change(
                    s.id,
                    "modify",
                    {"status": "dropped"},
                    "event_constraint",
                    f"{constraint.reason}: dropped.",
                )
            )
            continue
        cap = constraint.max_rpe if constraint.max_rpe is not None else cfg.rpe.easy
        if s.sport == "climb":
            rpe, minutes = cfg.climbing_defaults.technique
            kind, title = "climb_technique", "Technique climb"
        else:
            rpe, minutes, kind, title = (
                min(cfg.rpe.easy, cap),
                s.duration_min or 0,
                "easy",
                "Easy run (by feel)",
            )
        load = session_load(s.sport, kind, rpe, minutes, cfg.load_factors)
        steps = [Step(kind="steady", duration_s=minutes * 60, rpe=rpe)] if s.sport == "run" else []
        changes.append(
            Change(
                s.id,
                "modify",
                {
                    "kind": kind,
                    "title": title,
                    "steps": steps,
                    "duration_min": minutes,
                    "rpe": rpe,
                    "leg_load": load.leg,
                    "finger_load": load.finger,
                },
                "event_constraint",
                f"{constraint.reason}: eased to {title.lower()}.",
            )  # fmt: skip
        )
    for start, end in state.blocks:
        ramp = ramp_for_block((end - start).days + 1, cfg)
        first = monday(end + timedelta(days=1))  # the week you come back in is ramp week 0
        for offset, factor in enumerate(ramp.weekly_multipliers):
            if factor < 1:
                changes += _scale_week(
                    state, first + timedelta(weeks=offset), factor, "holiday_ramp",
                    f"Back from a {(end - start).days + 1}-day break: "
                    f"runs at {factor:.0%} of plan.",
                )  # fmt: skip
    return changes


def evaluate(state: PlanState, today: date, cfg: SchedulingConfig) -> list[Change]:
    """Every rule's changes for the current state, in a stable order."""
    return [
        *missed_once(state, today),
        *missed_two_plus(state, today),
        *ill_or_niggle(state, cfg),
        *event_constraints(state, cfg),
        *retest_suggested(state, cfg),
    ]
