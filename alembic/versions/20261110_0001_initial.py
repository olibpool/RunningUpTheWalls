from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261110_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "athlete",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("vdot", sa.Float(), nullable=False),
        sa.Column("vdot_source", sa.Text(), nullable=False),
        sa.Column("vdot_updated_at", sa.Text(), nullable=False),
        sa.Column("runs_per_week", sa.Integer(), nullable=False),
        sa.Column("climbing_target_per_week", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("max_weekly_load", sa.Float(), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="Europe/London"),
        sa.CheckConstraint("id = 1"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "plan",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("goal_distance_m", sa.Integer(), nullable=True),
        sa.Column("goal_date", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("config_snapshot", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("mode IN ('race', 'continuous')"),
        sa.CheckConstraint("status IN ('active', 'complete')"),
    )
    op.create_table(
        "session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Text(), nullable=False),
        sa.Column("start_time", sa.Text(), nullable=True),
        sa.Column("sport", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("steps_json", sa.Text(), nullable=False),
        sa.Column("planned_duration_min", sa.Integer(), nullable=True),
        sa.Column("planned_distance_m", sa.Integer(), nullable=True),
        sa.Column("planned_rpe", sa.Float(), nullable=True),
        sa.Column("leg_load", sa.Float(), nullable=False, server_default="0"),
        sa.Column("finger_load", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="planned"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("garmin_workout_id", sa.Text(), nullable=True),
        sa.Column("garmin_schedule_id", sa.Text(), nullable=True),
        sa.Column("gcal_event_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.id"], ondelete="CASCADE"),
        sa.CheckConstraint("sport IN ('run', 'climb', 'football', 'rest', 'other')"),
        sa.CheckConstraint("status IN ('planned', 'done', 'skipped', 'moved', 'dropped')"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_session_date"), "session", ["date"], unique=False)
    op.create_table(
        "activity",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("sport", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=False),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.Column("avg_hr", sa.Integer(), nullable=True),
        sa.Column("rpe", sa.Float(), nullable=True),
        sa.Column("load", sa.Float(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("raw_path", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "external_id", name="uq_activity_source_external_id"),
    )
    op.create_table(
        "adjustment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("before_json", sa.Text(), nullable=True),
        sa.Column("after_json", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "commitment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("sport", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Text(), nullable=True),
        sa.Column("end_date", sa.Text(), nullable=True),
        sa.Column("start_time", sa.Text(), nullable=True),
        sa.Column("duration_min", sa.Integer(), nullable=True),
        sa.Column("rpe", sa.Float(), nullable=True),
        sa.Column("severity", sa.Integer(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False, server_default="manual"),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("active", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("kind IN ('fixed', 'penalty', 'block', 'credit')"),
        sa.CheckConstraint("severity BETWEEN 1 AND 3"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "calendar_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.Text(), nullable=False),
        sa.Column("ends_at", sa.Text(), nullable=False),
        sa.Column("all_day", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id"),
    )
    op.create_table(
        "checkin",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Text(), nullable=False),
        sa.Column("feel", sa.Integer(), nullable=True),
        sa.Column("niggle", sa.Text(), nullable=True),
        sa.Column("ill", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resting_hr", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint("feel BETWEEN 1 AND 5"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date"),
    )
    op.create_table(
        "kv",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("kv")
    op.drop_table("checkin")
    op.drop_table("calendar_event")
    op.drop_table("commitment")
    op.drop_table("adjustment")
    op.drop_table("activity")
    op.drop_table("session")
    op.drop_table("plan")
    op.drop_table("athlete")
