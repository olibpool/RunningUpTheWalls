-- Initial schema. Never edit once applied; add a new numbered file instead.
CREATE TABLE athlete (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  name TEXT,
  vdot REAL NOT NULL,
  vdot_source TEXT NOT NULL,                 -- race | time_trial | manual
  vdot_updated_at TEXT NOT NULL,
  runs_per_week INTEGER NOT NULL,
  climbing_target_per_week INTEGER NOT NULL DEFAULT 2,
  max_weekly_load REAL,                      -- ceiling for continuous mode
  timezone TEXT NOT NULL DEFAULT 'Europe/London'
);

CREATE TABLE plan (
  id INTEGER PRIMARY KEY,
  mode TEXT NOT NULL CHECK (mode IN ('race','continuous')),
  goal_distance_m INTEGER,
  goal_date TEXT,
  start_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','complete')),
  created_at TEXT NOT NULL,
  config_snapshot TEXT NOT NULL              -- JSON of the config used
);

CREATE TABLE session (
  id INTEGER PRIMARY KEY,
  plan_id INTEGER NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
  date TEXT NOT NULL,                        -- local calendar date
  start_time TEXT,                           -- local HH:MM, nullable
  sport TEXT NOT NULL CHECK (sport IN ('run','climb','football','rest','other')),
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  steps_json TEXT NOT NULL,
  planned_duration_min INTEGER,
  planned_distance_m INTEGER,
  planned_rpe REAL,
  leg_load REAL NOT NULL DEFAULT 0,
  finger_load REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'planned'
    CHECK (status IN ('planned','done','skipped','moved','dropped')),
  reason TEXT,
  garmin_workout_id TEXT,
  garmin_schedule_id TEXT,
  gcal_event_id TEXT
);
CREATE INDEX idx_session_date ON session(date);

CREATE TABLE activity (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,                      -- fit | gpx | strava | garmin | manual
  external_id TEXT,
  sport TEXT NOT NULL,
  started_at TEXT NOT NULL,                  -- UTC ISO 8601
  duration_s INTEGER NOT NULL,
  distance_m REAL,
  avg_hr INTEGER,
  rpe REAL,
  load REAL,                                 -- session-RPE: rpe * minutes
  session_id INTEGER REFERENCES session(id) ON DELETE SET NULL,
  raw_path TEXT,
  UNIQUE (source, external_id)
);

CREATE TABLE adjustment (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  session_id INTEGER REFERENCES session(id) ON DELETE SET NULL,
  rule TEXT NOT NULL,
  before_json TEXT,
  after_json TEXT,
  reason TEXT NOT NULL
);

CREATE TABLE commitment (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('fixed','penalty','block','credit')),
  sport TEXT,
  title TEXT NOT NULL,
  weekday INTEGER,                           -- 0 = Monday, for recurring events
  start_date TEXT,                           -- inclusive, for dated events
  end_date TEXT,                             -- inclusive
  start_time TEXT,
  duration_min INTEGER,
  rpe REAL,
  severity INTEGER CHECK (severity BETWEEN 1 AND 3),
  source TEXT NOT NULL DEFAULT 'manual',     -- manual | gcal
  external_id TEXT,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE calendar_event (
  id INTEGER PRIMARY KEY,
  external_id TEXT NOT NULL UNIQUE,
  title TEXT,
  starts_at TEXT NOT NULL,
  ends_at TEXT NOT NULL,
  all_day INTEGER NOT NULL DEFAULT 0,
  fetched_at TEXT NOT NULL
);

CREATE TABLE checkin (
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL UNIQUE,
  feel INTEGER CHECK (feel BETWEEN 1 AND 5),
  niggle TEXT,
  ill INTEGER NOT NULL DEFAULT 0,
  resting_hr INTEGER,
  notes TEXT
);

CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
