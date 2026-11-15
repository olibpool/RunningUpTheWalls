-- Fever or chest symptoms: the illness rule clears running for a few days.
ALTER TABLE checkin ADD COLUMN fever INTEGER NOT NULL DEFAULT 0;
