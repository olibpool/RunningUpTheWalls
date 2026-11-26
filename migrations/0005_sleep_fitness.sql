-- Sleep from the watch or typed in, for the readiness hint.
ALTER TABLE checkin ADD COLUMN sleep_hours REAL;
-- Maximum heart rate, for estimating fitness from easy runs' heart rate.
ALTER TABLE athlete ADD COLUMN max_hr INTEGER;
