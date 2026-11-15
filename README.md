# Coachal

Coachal is a self-hosted training planner for runners and multi-sport athletes. It generates race plans from a runner's VDOT, adapts around missed sessions, tracks activities, and preserves recurring commitments such as football fixtures or blocked training windows.

## What it does

- Builds race plans (5K to marathon) and a goal-free continuous mode, scheduled around fixed
  commitments such as football and suggested climbing sessions, with a reason for every placement
- Refuses any plan that fails its safety checks (load rises, long-run share, hard-day spacing,
  pace order, taper)
- Ingests GPX runs (upload, or drop files in the inbox folder), matches them to sessions and
  adapts the plan: missed sessions, illness and niggles, drinks nights, holidays, heat
- Reads your Google Calendar (private iCal address) and moves sessions that clash with it
- Mobile-first web app, installable on the phone home screen; an ICS feed of the plan
- Runs on a Raspberry Pi: SQLite, one process, a 30-minute `runcoach sync` timer, nightly backups

## Project layout

- `src/runcoach/engine/` — training-plan logic, zones, progression, safety
- `src/runcoach/web/` — FastAPI app and routes
- `src/runcoach/db.py` — SQLite access and persistence
- `src/runcoach/cli.py` — command-line management for plans and commitments
- `tests/unit/` — regression tests covering the engine and persistence layer

## Prerequisites

- Python 3.12+
- `uv` for dependency management and running the project

## Quick start

From the repository root:

```bash
uv sync
```

This creates the environment and installs the project dependencies.

## Run the web app

Start the server:

```bash
uv run uvicorn runcoach.web.app:app --host 127.0.0.1 --port 8000 --reload
```

Then open http://127.0.0.1:8000 and, in this order:

1. **Events**: add football (or any weekly fixed commitment) so plans are built around it.
2. **Plan → Create a plan**: enter a recent race time (or a VDOT), preview, then save.
   A plan that fails the safety checks can't be saved; the preview says why.
3. **Week**: each day's sessions with paces and the reason they were placed there.
   Mark sessions done (optionally with RPE and minutes) or skipped.

Data lives in `runcoach.db` in the project root. `/healthz` reports the app and schema version.

## Use the CLI

Generate a plan (`--distance` is one of `5k`, `10k`, `half`, `marathon`). It prints a
week-by-week table with paces, and `--save` refuses to store a plan that fails the safety checks:

```bash
uv run python -m runcoach plan generate \
  --distance half \
  --date 2027-03-14 \
  --vdot 50 \
  --runs 4 \
  --current-km 20 \
  --save
```

Add a manual commitment:

```bash
uv run python -m runcoach commitment add \
  --kind fixed \
  --title "Football" \
  --sport football \
  --weekday 1 \
  --start-time "19:00" \
  --duration 90 \
  --rpe 6
```

Store an activity file, run a sync (inbox, calendar, weather, matching, adaptation, re-plan),
or back up the database:

```bash
uv run python -m runcoach ingest ~/Downloads/morning-run.gpx
uv run python -m runcoach sync
uv run python -m runcoach backup
```

List commitments:

```bash
uv run python -m runcoach commitment list
```

Add a penalty or a travel block:

```bash
uv run python -m runcoach event add --kind penalty --date 2026-11-14 --severity 2
uv run python -m runcoach event add --kind block --start 2026-12-20 --end 2026-12-27 --minimal-plan
uv run python -m runcoach event list
```

## Run the test suite

```bash
uv run pytest -q
uv run pytest --update-golden      # after reviewing an intended change to plan output
HYPOTHESIS_PROFILE=ci uv run pytest  # 200 property-test examples (pi: 50, default: 25)
```

Or use the project validation stack:

```bash
uv run ruff check .
uv run mypy src
uv run pytest -q
```

## Deploying

See `deploy/README.md` for the Raspberry Pi set-up (systemd units, Tailscale, backups and
restore). Settings go in `config/local.toml` or `RUNCOACH_SECTION__KEY` environment variables; the
Google Calendar address is a secret and belongs only there.

## Local data

The app uses a SQLite database created under the project root by default. The database is created automatically when the app or CLI touches it for the first time.

## Notes

This is a local-first, self-hosted project aimed at running on a Raspberry Pi or similar small server. It is designed to stay deterministic, keep runtime logic pure, and separate training-engine calculations from database and web concerns.
