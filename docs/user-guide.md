# Coachal user guide

The in-app version of the day-to-day guide is at `/guide` (the Guide link in the app). This file
adds what only matters when you run it yourself: setup, configuration, the command line and backups.

## Install and run

```bash
uv sync
uv run uvicorn runcoach.web.app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. The database (`runcoach.db`) is created on first use. For a
Raspberry Pi, with the sync and backup timers, follow `deploy/README.md`.

## Using the app

1. **Settings**: set your VDOT, runs per week and climbing sessions per week.
2. **Events**: add fixed commitments (football), drinks nights and holidays.
3. **Plan, Create a plan**: pick a distance and race date, or choose continuous improvement.
   A plan that fails the safety checks cannot be saved, and the preview says why.
4. **Home** shows today and what needs attention. **Week** shows each day with paces and the reason
   for each placement. Mark sessions done or skipped, or move them.
5. **Activities**: runs arrive from Garmin, or upload a FIT or GPX file; each is matched to a
   session. Unmatched runs can be assigned by hand.
6. **Check-in**: log how you feel, niggles, illness and resting heart rate.

The plan adapts to missed sessions, illness, drinks, holidays and heat, and records the reason for
every change on the session page. Your VDOT never changes unless you submit a time trial.

## Configuration

Defaults are in `config/defaults.toml` and every tunable number lives there. Override them in
`config/local.toml` (not committed) or with environment variables named `RUNCOACH_SECTION__KEY`.
The usual personal settings:

| Setting | Purpose |
|---|---|
| `[availability]` | The weekday and weekend windows sessions may be placed in |
| `[gcal] ics_url` | Your Google Calendar's secret iCal address. A secret: never commit it |
| `[weather] enabled`, latitude, longitude | Heat adjustment |
| `[inbox] path` | Folder watched for dropped FIT or GPX files |
| `[storage] db_path` | Database location (default `runcoach.db`) |
| `[garmin] enabled` | Pull activities and resting HR from Garmin Connect |
| `[export] backend` | `garmin` pushes the next `push_horizon_days` of runs to Garmin Connect |
| `[compliance]` | How close a rep must be to its pace range to count as on target |

## Garmin

Coachal works through Garmin Connect: the watch syncs to Garmin Connect through the phone as usual,
and every `runcoach sync` pulls new activities (original FIT files, with laps) and resting heart
rate, then pushes the coming week's runs as structured workouts with pace targets.

```bash
uv run python -m runcoach garmin login    # once: password and MFA prompt; saves only a token
uv run python -m runcoach garmin status   # checks the saved token still works
```

The token lives in `[garmin] token_dir` (default `~/.config/runcoach/garmin`), never in the repo.
The library is unofficial; a Garmin change shows as an error in the sync report and nothing else
stops. Without Garmin Connect, each run session offers a `.fit` workout file to copy to the
watch's `GARMIN/NewFiles` folder.

## Command line

```bash
uv run python -m runcoach plan generate --distance half --date 2027-03-14 --vdot 50 --runs 4 --current-km 20 --save
uv run python -m runcoach commitment add --kind fixed --title Football --sport football --weekday 1 --start-time 19:00 --duration 90 --rpe 6
uv run python -m runcoach event add --kind penalty --date 2026-11-14 --severity 2
uv run python -m runcoach ingest ~/Downloads/run.fit   # .fit, .gpx, or Garmin's .zip
uv run python -m runcoach sync      # inbox, calendar, weather, matching, adaptation, re-plan
uv run python -m runcoach backup
```

## Not built yet

Strava, writing back to Google Calendar and the AI explainer. `docs/next-steps.md` lists the state of each phase and what is waiting on inputs.
