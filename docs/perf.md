# Performance baseline

- Baseline captured after the initial scaffold and planning-engine setup.
- Phase 2 note: plan generation route added with a lightweight FastAPI router and no persistent background services.
- Phase 2 ingestion note: added a GPX upload route and parser that keeps the request path in-process and memory-light; no long-running worker or scheduler was introduced.
- Idle app memory target: keep the process under 30 MB resident growth.
- Current baseline: not yet measured in a running Pi/uvicorn process; the route layer remains intentionally minimal and the parser stays in the Python standard library.
- Phase 2c local check (2026-11-11): Uvicorn `/healthz` returned 200; the idle Windows worker used 56.58 MiB working set after startup. This is not a Pi measurement and does not establish resident growth against a pre-start baseline.

Use the following command on the Pi after deployment:

ps -o rss= -p $(pgrep -f uvicorn)
- Phase 3 basic UI local check (2026-11-15): uvicorn idle working set 64.9 MiB on Windows after loading /healthz, /week, /plan/new and /events (+8 MiB over the Phase 2c reading; Jinja templates and the Phase 1 engine). Still not a Pi measurement.
- Auto-sync and Garmin web login local check (2026-11-16): uvicorn idle working set 79.0 MiB on Windows after loading /healthz, /, /week, /plan, /settings and /sync/chip (+14 MiB over the Phase 3 reading, which loaded fewer pages; the background sync loop is one asyncio task that sleeps 60 s, and syncs run in a worker thread). Still not a Pi measurement.
- UX redesign local check (2026-11-16): uvicorn idle working set 79.8 MiB on Windows after loading /, /week, /plan, /progress, /events and /activities (the new pages add no dependencies or background work). Still not a Pi measurement.
