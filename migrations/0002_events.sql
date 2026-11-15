-- Travel blocks where the athlete still wants a few short runs.
ALTER TABLE commitment ADD COLUMN minimal_plan INTEGER NOT NULL DEFAULT 0;
