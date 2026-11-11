from __future__ import annotations

from datetime import date

from runcoach.domain.models import PlannedSession, Step


def build_session(kind: str, ctx: object, zones: object, cfg: object) -> PlannedSession:
    if kind == "easy":
        steps = [
            Step(
                kind="steady",
                duration_s=30 * 60,
                pace_min_s_per_km=260.0,
                pace_max_s_per_km=340.0,
            )
        ]
        return PlannedSession(
            date=ctx.date if hasattr(ctx, "date") else date.today(),
            sport="run",
            kind="easy",
            title="Easy run",
            steps=steps,
            duration_min=30,
            distance_m=4000,
            rpe=3.0,
            leg_load=90.0,
            finger_load=0.0,
            reason="Easy aerobic effort",
        )
    if kind == "long":
        return PlannedSession(
            date=ctx.date if hasattr(ctx, "date") else date.today(),
            sport="run",
            kind="long",
            title="Long run",
            steps=[
                Step(
                    kind="steady",
                    duration_s=45 * 60,
                    pace_min_s_per_km=290.0,
                    pace_max_s_per_km=360.0,
                )
            ],
            duration_min=45,
            distance_m=6000,
            rpe=4.0,
            leg_load=180.0,
            finger_load=0.0,
            reason="Long aerobic session",
        )
    return PlannedSession(
        date=ctx.date if hasattr(ctx, "date") else date.today(),
        sport="run",
        kind=kind,
        title=kind.replace("_", " ").title(),
        steps=[Step(kind="work", duration_s=20 * 60)],
        duration_min=20,
        distance_m=3000,
        rpe=6.0,
        leg_load=120.0,
        finger_load=0.0,
        reason="Quality session",
    )
