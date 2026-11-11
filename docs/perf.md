# Performance baseline

- Baseline captured after the initial scaffold and planning-engine setup.
- Phase 2 note: plan generation route added with a lightweight FastAPI router and no persistent background services.
- Phase 2 ingestion note: added a GPX upload route and parser that keeps the request path in-process and memory-light; no long-running worker or scheduler was introduced.
- Idle app memory target: keep the process under 30 MB resident growth.
- Current baseline: not yet measured in a running Pi/uvicorn process; the route layer remains intentionally minimal and the parser stays in the Python standard library.

Use the following command on the Pi after deployment:

ps -o rss= -p $(pgrep -f uvicorn)
