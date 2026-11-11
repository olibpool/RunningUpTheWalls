from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from runcoach.config import SchedulingConfig
from runcoach.domain.models import PlannedSession
from runcoach.engine.load import session_load


@dataclass(frozen=True)
class Busy:
    start: datetime
    end: datetime
    title: str


@dataclass(frozen=True)
class FixedInstance:
    date: date
    title: str
    sport: str
    start_time: str
    duration_min: int
    rpe: float | None = None


@dataclass(frozen=True)
class ScheduledSession:
    session: PlannedSession | None
    fixed: FixedInstance | None
    date: date
    start_time: str
    reason: str


@dataclass(frozen=True)
class DroppedSession:
    session: PlannedSession
    reason: str


@dataclass(frozen=True)
class ScheduleResult:
    placed: list[ScheduledSession]
    dropped: list[DroppedSession]
    warnings: list[str]
    relaxed: bool = False


@dataclass(frozen=True)
class _Placement:
    session: PlannedSession | None
    fixed: FixedInstance | None
    start: datetime
    end: datetime
    kind: str
    sport: str
    title: str

    @property
    def is_fixed(self) -> bool:
        return self.fixed is not None


@dataclass(frozen=True)
class _Candidate:
    placement: _Placement
    cost: float
    preference: int


@dataclass(frozen=True)
class _Attempt:
    placements: list[_Placement]
    dropped: list[PlannedSession]
    missing_long: bool
    objective: tuple[int, int, float, float]


_QUALITY_KINDS = {"tempo", "intervals"}
_HARD_LEG_KINDS = {"football", "long", "tempo", "intervals"}
_CLIMB_KINDS = ("climb_power", "climb_volume", "climb_technique")


def _is_hard_leg(placement: _Placement) -> bool:
    return placement.sport == "football" or placement.kind in _HARD_LEG_KINDS


def _is_limit_climb(placement: _Placement) -> bool:
    return placement.kind == "climb_power"


def _session_priority(session: PlannedSession) -> tuple[int, date, str]:
    if session.kind == "long":
        priority = 0
    elif session.kind in _QUALITY_KINDS:
        priority = 1
    elif session.kind == "climb_power":
        priority = 2
    elif session.kind == "climb_volume":
        priority = 3
    elif session.kind == "climb_technique":
        priority = 4
    elif session.kind == "easy":
        priority = 5
    elif session.kind == "recovery":
        priority = 6
    else:
        priority = 7
    return priority, session.date, session.title


def _drop_cost(session: PlannedSession, cfg: SchedulingConfig) -> int:
    if session.kind in {"easy", "recovery"}:
        return cfg.scheduler.drop_cost_easy
    if session.sport == "climb":
        return cfg.scheduler.drop_cost_climb
    if session.kind in _QUALITY_KINDS:
        return cfg.scheduler.drop_cost_quality
    return cfg.scheduler.drop_cost_other


def _climbing_sessions(
    week_start: date,
    sessions: list[PlannedSession],
    climb_target: int,
    cfg: SchedulingConfig,
) -> list[PlannedSession]:
    if not 0 <= climb_target <= len(_CLIMB_KINDS):
        raise ValueError(f"climb_target must be between 0 and {len(_CLIMB_KINDS)}")
    result = list(sessions)
    existing = {session.kind for session in sessions if session.sport == "climb"}
    for kind in _CLIMB_KINDS[:climb_target]:
        if kind in existing:
            continue
        defaults = getattr(cfg.climbing_defaults, kind.removeprefix("climb_"))
        rpe, duration = defaults
        load = session_load("climb", kind, rpe, duration, cfg.load_factors)
        result.append(
            PlannedSession(
                date=week_start,
                sport="climb",
                kind=kind,
                title=f"{kind.removeprefix('climb_').title()} climb",
                duration_min=duration,
                rpe=rpe,
                leg_load=load.leg,
                finger_load=load.finger,
                reason="",
            )
        )
    return result


def _fixed_placement(instance: FixedInstance, tz: ZoneInfo) -> _Placement:
    start_clock = time.fromisoformat(instance.start_time)
    start = datetime.combine(instance.date, start_clock, tzinfo=tz)
    return _Placement(
        session=None,
        fixed=instance,
        start=start,
        end=start + timedelta(minutes=instance.duration_min),
        kind=instance.sport,
        sport=instance.sport,
        title=instance.title,
    )


def _gap_hours(left: _Placement, right: _Placement) -> float:
    return abs((left.start - right.start).total_seconds()) / 3600.0


def _can_place(
    candidate: _Placement,
    placements: list[_Placement],
    busy: list[Busy],
    prior_tail: list[PlannedSession],
    cfg: SchedulingConfig,
    minimum_hard_hours: int,
    buffer_minutes: int,
) -> bool:
    for busy_event in busy:
        busy_start = busy_event.start
        busy_end = busy_event.end
        if busy_start.tzinfo is None or busy_end.tzinfo is None:
            raise ValueError("Busy event datetimes must be timezone-aware")
        if candidate.start < busy_end + timedelta(
            minutes=buffer_minutes
        ) and candidate.end > busy_start - timedelta(minutes=buffer_minutes):
            return False

    for other in placements:
        if candidate.start.date() == other.start.date():
            required_gap = (
                buffer_minutes
                if candidate.is_fixed or other.is_fixed
                else cfg.scheduler.min_hours_same_day_gap * 60
            )
            gap_minutes = max(
                (candidate.start - other.end).total_seconds() / 60.0,
                (other.start - candidate.end).total_seconds() / 60.0,
            )
            if gap_minutes < required_gap:
                return False

        if (
            _is_hard_leg(candidate)
            and _is_hard_leg(other)
            and _gap_hours(candidate, other) < minimum_hard_hours
        ):
            return False
        if (
            _is_limit_climb(candidate)
            and _is_limit_climb(other)
            and _gap_hours(candidate, other) < cfg.scheduler.min_hours_between_limit_climbs
        ):
            return False

    if _is_hard_leg(candidate):
        for previous in prior_tail:
            if previous.kind in _HARD_LEG_KINDS or previous.sport == "football":
                if previous.start_time is not None:
                    prior_start = datetime.combine(
                        previous.date,
                        time.fromisoformat(previous.start_time),
                        tzinfo=candidate.start.tzinfo,
                    )
                    hours = abs((candidate.start - prior_start).total_seconds()) / 3600.0
                else:
                    day_gap = abs((candidate.start.date() - previous.date).days)
                    hours = max(0, day_gap - 1) * 24
                if hours < minimum_hard_hours:
                    return False
    if _is_limit_climb(candidate):
        for previous in prior_tail:
            if previous.kind == "climb_power":
                if previous.start_time is not None:
                    prior_start = datetime.combine(
                        previous.date,
                        time.fromisoformat(previous.start_time),
                        tzinfo=candidate.start.tzinfo,
                    )
                    hours = abs((candidate.start - prior_start).total_seconds()) / 3600.0
                else:
                    day_gap = abs((candidate.start.date() - previous.date).days)
                    hours = max(0, day_gap - 1) * 24
                if hours < cfg.scheduler.min_hours_between_limit_climbs:
                    return False

    occupied_days = {placement.start.date() for placement in placements}
    if candidate.start.date() not in occupied_days and len(occupied_days) >= 6:
        return False
    return True


def _soft_cost(
    candidate: _Placement,
    placements: list[_Placement],
    cfg: SchedulingConfig,
) -> float:
    cost = 0.0
    if candidate.kind == "long" and candidate.start.weekday() < 5:
        cost += cfg.scheduler.long_run_weekday_cost

    long_runs = [item for item in placements if item.kind == "long"]
    if candidate.kind == "climb_power" and any(
        long_run.start.date() == candidate.start.date() + timedelta(days=1)
        for long_run in long_runs
    ):
        cost += cfg.scheduler.limit_climb_before_long_cost

    if candidate.sport == "climb" and any(
        item.kind in _QUALITY_KINDS | {"long"} and item.start.date() == candidate.start.date()
        for item in placements
    ):
        cost += cfg.scheduler.hard_run_climb_same_day_cost
    if candidate.sport == "climb" and any(
        item.sport == "climb" and item.start.date() == candidate.start.date() for item in placements
    ):
        cost += cfg.scheduler.same_day_climb_cost
    if candidate.sport == "climb":
        for item in placements:
            if item.sport == "climb":
                actual_gap = abs((candidate.start.date() - item.start.date()).days)
                cost += abs(actual_gap - cfg.scheduler.climbing_spacing_days)
        if candidate.kind == "climb_volume":
            football = next(
                (item for item in placements if item.sport == "football"),
                None,
            )
            if football is not None:
                preferred_day = football.start.date() + timedelta(
                    days=1 + cfg.scheduler.climbing_spacing_days
                )
                cost += abs((candidate.start.date() - preferred_day).days)
    if candidate.kind in _QUALITY_KINDS:
        target = cfg.scheduler.quality_gap_target_hours
        for other in placements:
            if other.sport == "football":
                gap = _gap_hours(candidate, other)
                if cfg.scheduler.min_hours_between_hard_leg <= gap < target:
                    cost += gap - cfg.scheduler.min_hours_between_hard_leg
            elif other.kind == "long":
                gap = _gap_hours(candidate, other)
                if cfg.scheduler.min_hours_between_hard_leg <= gap < target:
                    cost += (target - gap) / target
            else:
                continue
    return cost


def _candidate_starts(
    session: PlannedSession,
    day: date,
    windows: list[tuple[str, str]],
    placements: list[_Placement],
    busy: list[Busy],
    prior_tail: list[PlannedSession],
    cfg: SchedulingConfig,
    minimum_hard_hours: int,
    tz: ZoneInfo,
) -> list[datetime]:
    if session.duration_min is None or session.duration_min <= 0:
        raise ValueError(f"Session {session.title!r} must have a positive duration")
    duration = timedelta(minutes=session.duration_min)
    buffer = timedelta(minutes=cfg.availability.buffer_minutes)
    same_day_gap = timedelta(hours=cfg.scheduler.min_hours_same_day_gap)
    candidates: set[datetime] = set()

    for window_start, window_end in windows:
        start_clock = time.fromisoformat(window_start)
        end_clock = time.fromisoformat(window_end)
        if start_clock >= end_clock:
            raise ValueError(
                f"Availability window must end after it starts: {window_start}-{window_end}"
            )
        earliest = datetime.combine(day, start_clock, tzinfo=tz)
        latest = datetime.combine(day, end_clock, tzinfo=tz)
        window_candidates: set[datetime] = {earliest}
        for event in busy:
            event_start = event.start.astimezone(tz)
            event_end = event.end.astimezone(tz)
            if event_start.date() == day:
                window_candidates.add(event_end + buffer)
                window_candidates.add(event_start - duration - buffer)
        for other in placements:
            if other.start.date() != day:
                continue
            gap = buffer if other.is_fixed else same_day_gap
            window_candidates.add(other.end + gap)
            window_candidates.add(other.start - duration - gap)
        for other in placements:
            if _is_hard_leg(other):
                window_candidates.add(other.start + timedelta(hours=minimum_hard_hours))
                window_candidates.add(other.start - timedelta(hours=minimum_hard_hours))
            if _is_limit_climb(other):
                separation = timedelta(hours=cfg.scheduler.min_hours_between_limit_climbs)
                window_candidates.add(other.start + separation)
                window_candidates.add(other.start - separation)
        for previous in prior_tail:
            if previous.start_time is None:
                continue
            previous_start = datetime.combine(
                previous.date,
                time.fromisoformat(previous.start_time),
                tzinfo=tz,
            )
            if previous.kind in _HARD_LEG_KINDS or previous.sport == "football":
                separation = timedelta(hours=minimum_hard_hours)
                window_candidates.add(previous_start + separation)
            if previous.kind == "climb_power":
                separation = timedelta(hours=cfg.scheduler.min_hours_between_limit_climbs)
                window_candidates.add(previous_start + separation)

        candidates.update(
            candidate
            for candidate in window_candidates
            if earliest <= candidate and candidate + duration <= latest
        )
    return sorted(candidates)


def _candidate_reason(
    placement: _Placement,
    placements: list[_Placement],
    busy: list[Busy],
) -> str:
    day = placement.start.strftime("%A")
    reasons = [f"{day} availability"]
    if placement.kind == "long" and placement.start.weekday() >= 5:
        reasons.append("weekend long-run slot")
    if placement.sport == "climb" and any(
        item.sport == "football" and placement.start.date() == item.start.date() + timedelta(days=1)
        for item in placements
    ):
        reasons.append("day after football")
    if placement.kind in _QUALITY_KINDS:
        if any(item.sport == "football" for item in placements):
            reasons.append("spaced from football")
        if any(item.kind == "long" for item in placements):
            reasons.append("spaced before the long run")
        if placement.session is not None:
            for event in busy:
                if (
                    event.start.astimezone(placement.start.tzinfo).date() == placement.session.date
                    and placement.start.date() != placement.session.date
                ):
                    reasons.append(f"moved around {event.title}")
    return "; ".join(reasons)


def _make_candidates(
    session: PlannedSession,
    week_start: date,
    placements: list[_Placement],
    busy: list[Busy],
    prior_tail: list[PlannedSession],
    cfg: SchedulingConfig,
    minimum_hard_hours: int,
    tz: ZoneInfo,
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        windows = cfg.availability.weekend if day.weekday() >= 5 else cfg.availability.weekday
        starts = _candidate_starts(
            session,
            day,
            windows,
            placements,
            busy,
            prior_tail,
            cfg,
            minimum_hard_hours,
            tz,
        )
        for start in starts:
            placement = _Placement(
                session=session,
                fixed=None,
                start=start,
                end=start + timedelta(minutes=session.duration_min or 0),
                kind=session.kind,
                sport=session.sport,
                title=session.title,
            )
            if not _can_place(
                placement,
                placements,
                busy,
                prior_tail,
                cfg,
                minimum_hard_hours,
                cfg.availability.buffer_minutes,
            ):
                continue
            cost = _soft_cost(placement, placements, cfg)
            preference = 0
            if placement.kind == "long" and day.weekday() != 6:
                preference += 1
            if placement.sport == "climb" and not any(
                item.sport == "football" and day == item.start.date() + timedelta(days=1)
                for item in placements
            ):
                preference += 1
            candidates.append(_Candidate(placement, cost, preference))
    return sorted(
        candidates,
        key=lambda item: (
            item.cost,
            item.preference,
            item.placement.start,
        ),
    )


def _attempt_schedule(
    sessions: list[PlannedSession],
    fixed_placements: list[_Placement],
    busy: list[Busy],
    prior_tail: list[PlannedSession],
    cfg: SchedulingConfig,
    minimum_hard_hours: int,
    week_start: date,
    tz: ZoneInfo,
) -> _Attempt:
    best: _Attempt | None = None

    def search(
        index: int,
        placements: list[_Placement],
        dropped: list[PlannedSession],
        missing_long: bool,
        drop_cost: int,
        soft_cost: float,
    ) -> None:
        nonlocal best
        if best is not None:
            if int(missing_long) > best.objective[0]:
                return
            if int(missing_long) == best.objective[0] and len(dropped) > best.objective[1]:
                return
            if (
                int(missing_long) == best.objective[0]
                and len(dropped) == best.objective[1]
                and drop_cost + soft_cost > best.objective[2] + best.objective[3]
            ):
                return

        if index == len(sessions):
            objective = (int(missing_long), len(dropped), float(drop_cost), soft_cost)
            if best is None or objective < best.objective:
                best = _Attempt(placements.copy(), dropped.copy(), missing_long, objective)
            return

        session = sessions[index]
        candidates = _make_candidates(
            session,
            week_start,
            placements,
            busy,
            prior_tail,
            cfg,
            minimum_hard_hours,
            tz,
        )
        for candidate in candidates:
            search(
                index + 1,
                [*placements, candidate.placement],
                dropped,
                missing_long,
                drop_cost,
                soft_cost + candidate.cost,
            )

        if session.kind == "long":
            search(index + 1, placements, dropped, True, drop_cost, soft_cost)
        else:
            search(
                index + 1,
                placements,
                [*dropped, session],
                missing_long,
                drop_cost + _drop_cost(session, cfg),
                soft_cost,
            )

    search(0, fixed_placements, [], False, 0, 0.0)
    if best is None:
        return _Attempt(fixed_placements, [], True, (1, 0, 0.0, 0.0))
    return best


def _fixed_conflict_warnings(
    fixed_placements: list[_Placement],
    busy: list[Busy],
    cfg: SchedulingConfig,
) -> list[str]:
    warnings: list[str] = []
    buffer = timedelta(minutes=cfg.availability.buffer_minutes)
    for fixed in fixed_placements:
        for event in busy:
            event_start = event.start.astimezone(fixed.start.tzinfo)
            event_end = event.end.astimezone(fixed.start.tzinfo)
            if fixed.start < event_end + buffer and fixed.end > event_start - buffer:
                warnings.append(
                    f"Fixed commitment {fixed.title} conflicts with busy event {event.title}; "
                    "the fixed time was kept."
                )
    for index, fixed in enumerate(fixed_placements):
        for other in fixed_placements[index + 1 :]:
            gap_minutes = max(
                (fixed.start - other.end).total_seconds() / 60.0,
                (other.start - fixed.end).total_seconds() / 60.0,
            )
            if gap_minutes < cfg.availability.buffer_minutes:
                warnings.append(
                    f"Fixed commitments {fixed.title} and {other.title} overlap or lack "
                    "the configured buffer; both fixed times were kept."
                )
            if (
                fixed.start.date() == other.start.date()
                and gap_minutes < cfg.scheduler.min_hours_same_day_gap * 60
            ):
                warnings.append(
                    f"Fixed commitments {fixed.title} and {other.title} are too close together; "
                    "both fixed times were kept."
                )
            if (
                _is_hard_leg(fixed)
                and _is_hard_leg(other)
                and _gap_hours(fixed, other) < cfg.scheduler.min_hours_between_hard_leg
            ):
                warnings.append(
                    f"Fixed hard-leg commitments {fixed.title} and {other.title} violate "
                    "the minimum spacing; both fixed times were kept."
                )
            if (
                _is_limit_climb(fixed)
                and _is_limit_climb(other)
                and _gap_hours(fixed, other) < cfg.scheduler.min_hours_between_limit_climbs
            ):
                warnings.append(
                    f"Fixed limit climbs {fixed.title} and {other.title} violate "
                    "the minimum spacing; both fixed times were kept."
                )
    return warnings


def schedule_week(
    week_start: date,
    unplaced: list[PlannedSession],
    climb_target: int,
    fixed: list[FixedInstance],
    busy: list[Busy],
    prior_tail: list[PlannedSession],
    cfg: SchedulingConfig,
    *,
    timezone_name: str = "Europe/London",
) -> ScheduleResult:
    """Place one week's sessions deterministically while enforcing hard scheduling constraints."""
    if week_start.weekday() != 0:
        raise ValueError("week_start must be a Monday")
    if any(instance.duration_min <= 0 for instance in fixed):
        raise ValueError("Fixed commitment duration must be positive")
    if any(event.start.tzinfo is None or event.end.tzinfo is None for event in busy):
        raise ValueError("Busy event datetimes must be timezone-aware")
    if any(event.end <= event.start for event in busy):
        raise ValueError("Busy event end must follow its start")
    tz = ZoneInfo(timezone_name)
    sessions = _climbing_sessions(week_start, unplaced, climb_target, cfg)
    sessions.sort(key=_session_priority)
    fixed_placements = [
        _fixed_placement(instance, tz)
        for instance in fixed
        if week_start <= instance.date < week_start + timedelta(days=7)
    ]
    fixed_placements.sort(key=lambda item: item.start)
    week_busy = [
        event
        for event in busy
        if week_start <= event.start.astimezone(tz).date() < week_start + timedelta(days=7)
    ]
    strict = _attempt_schedule(
        sessions,
        fixed_placements,
        week_busy,
        prior_tail,
        cfg,
        cfg.scheduler.min_hours_between_hard_leg,
        week_start,
        tz,
    )
    chosen = strict
    relaxed = False
    if strict.dropped or strict.missing_long:
        relaxed_attempt = _attempt_schedule(
            sessions,
            fixed_placements,
            week_busy,
            prior_tail,
            cfg,
            cfg.scheduler.relaxed_hours_between_hard_leg,
            week_start,
            tz,
        )
        if relaxed_attempt.objective < strict.objective:
            chosen = relaxed_attempt
            relaxed = True

    warnings = _fixed_conflict_warnings(
        fixed_placements,
        week_busy,
        cfg,
    )
    if relaxed:
        warnings.append(
            "Hard-leg spacing relaxed to "
            f"{cfg.scheduler.relaxed_hours_between_hard_leg} hours for this week."
        )
    if chosen.missing_long:
        warnings.append(f"Long run could not be placed for week starting {week_start.isoformat()}.")
    for session in chosen.dropped:
        warnings.append(f"Dropped {session.title}: no feasible slot remained after prioritization.")

    placed: list[ScheduledSession] = []
    for placement in sorted(chosen.placements, key=lambda item: item.start):
        reason = (
            "Fixed commitment"
            if placement.is_fixed
            else _candidate_reason(placement, chosen.placements, week_busy)
        )
        placed.append(
            ScheduledSession(
                session=(
                    placement.session.model_copy(
                        update={
                            "date": placement.start.date(),
                            "start_time": placement.start.strftime("%H:%M"),
                            "reason": reason,
                        }
                    )
                    if placement.session is not None
                    else None
                ),
                fixed=placement.fixed,
                date=placement.start.date(),
                start_time=placement.start.strftime("%H:%M"),
                reason=reason,
            )
        )
    ordered_dropped = sorted(chosen.dropped, key=lambda session: _drop_cost(session, cfg))
    dropped = [
        DroppedSession(
            session=session, reason="Dropped to preserve a rest day and satisfy constraints."
        )
        for session in ordered_dropped
    ]
    return ScheduleResult(placed=placed, dropped=dropped, warnings=warnings, relaxed=relaxed)
