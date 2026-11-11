# Coachal

Coachal is a self-hosted training planner for runners and multi-sport athletes. It generates race plans from a runner's VDOT, adapts around missed sessions, tracks activities, and preserves recurring commitments such as football fixtures or blocked training windows.

## What it does

- Builds training plans from a goal distance, date, and current fitness profile
- Models session load, progression, and pace/zone calculations
- Stores plan data in SQLite with migrations via Alembic
- Accepts activity ingestion from GPX and other structured sources
- Supports commitment and calendar-style blocking data
- Exposes a FastAPI API for local web usage and automation

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

Start the local API server:

```bash
uv run uvicorn runcoach.web.app:app --host 127.0.0.1 --port 8000 --reload
```

Then open:

- http://127.0.0.1:8000/health
- http://127.0.0.1:8000/docs

The health route confirms the app is live and the Swagger UI exposes the available endpoints.

## Use the CLI

Generate a plan:

```bash
uv run python -m runcoach.cli plan generate \
  --distance 21.1 \
  --date 2027-03-14 \
  --vdot 50 \
  --runs 4 \
  --current-km 20 \
  --save
```

Add a manual commitment:

```bash
uv run python -m runcoach.cli commitment add \
  --kind fixed \
  --title "Football" \
  --sport football \
  --weekday 1 \
  --start-time "19:00" \
  --duration 90 \
  --rpe 6
```

List commitments:

```bash
uv run python -m runcoach.cli commitment list
```

Add a penalty or a travel block:

```bash
uv run python -m runcoach.cli event add --kind penalty --date 2026-11-14 --severity 2
uv run python -m runcoach.cli event add --kind block --start 2026-12-20 --end 2026-12-27 --minimal-plan
uv run python -m runcoach.cli event list
```

## Run the test suite

```bash
uv run pytest -q
```

Or use the project validation stack:

```bash
uv run ruff check .
uv run mypy src
uv run pytest -q
```

## Local data

The app uses a SQLite database created under the project root by default. The database is created automatically when the app or CLI touches it for the first time.

## Notes

This is a local-first, self-hosted project aimed at running on a Raspberry Pi or similar small server. It is designed to stay deterministic, keep runtime logic pure, and separate training-engine calculations from database and web concerns.
