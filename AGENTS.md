# Working rules

1. Read the whole document once, then each phase before writing code.
2. Never guess a third-party API. Read the library README or source first.
3. Keep engine/pure functions free from I/O, time.now, and database access.
4. Only use the listed dependencies and avoid adding ORM or Numpy/Pandas/SciPy tooling.
5. Safety limits live in code with tests.
6. Every tunable number lives in config/defaults.toml and is read through config.py.
7. Never commit secrets; keep tokens outside the repo under ~/.config/runcoach/.
8. Store datetimes as ISO 8601 UTC strings and planned sessions as local dates in Europe/London.
9. Record idle resident memory in docs/perf.md after each phase.
10. Ask the human if anything is ambiguous.
