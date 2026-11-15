-- Laps recorded by the watch (FIT files), for scoring a run against its planned steps.
ALTER TABLE activity ADD COLUMN laps_json TEXT NOT NULL DEFAULT '[]';

-- Workouts pushed to Garmin Connect. No foreign key: the row must outlive a deleted or
-- replaced session so the next sync can still remove its workout from the watch.
CREATE TABLE garmin_push (
  session_id INTEGER PRIMARY KEY,
  workout_id TEXT NOT NULL,
  schedule_id TEXT,
  date TEXT NOT NULL,                        -- local date the workout is scheduled on
  hash TEXT NOT NULL,                        -- of the pushed workout; a change re-pushes
  pushed_at TEXT NOT NULL                    -- UTC ISO 8601
);
