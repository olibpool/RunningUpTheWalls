# Next steps: handover for the next Claude session

Last updated 2026-11-15 on branch `phase1-completion`. Read `data/Strategic Plan.md`, then
`data/Implementation Plan.md`, then `AGENTS.md`, then this file. Task IDs (P2-T4 etc.) refer
to the Implementation Plan. Where this file and the plans disagree, this file records a
decision the human already made. Don't reopen it without asking.

## How to work in this repo

- **Run the checks with the venv Python.** Windows Application Control blocks the
  `pytest`/`mypy` launchers that `uv run` spawns:
  `.venv/Scripts/python.exe -m pytest -q`, `.venv/Scripts/python.exe -m mypy src`,
  `uv run ruff check . && uv run ruff format --check .` (ruff works through uv).
- `pytest --update-golden` rewrites `tests/fixtures/golden_half_plan.json`. Only run it after
  reading the diff and agreeing the plan change is intended.
- `HYPOTHESIS_PROFILE=ci` gives 200 property examples, `pi` gives 50, and the default gives 25.
  The 200-example race-plan run takes about 5 minutes (see "Scheduler speed" below).
- Commit once per task with the task ID in the message (`P2-T4: ...`). Never bundle work into
  a "WIP" commit. The commit that built most of Phase 2c was one undifferentiated "WIP".
- Ask the human when the plans are ambiguous. Several defaults below are flagged as
  unsourced and need their review.

## Decisions already taken (don't reopen)

| # | Decision |
|---|---|
| D1 | **No Alembic or SQLAlchemy.** Migrations are numbered `migrations/NNNN_name.sql` files, applied by `db.migrate()` through `PRAGMA user_version`, one transaction each. Never edit an applied file; add a new one. All SQL stays in `db.py` behind model-in, model-out functions (that is the seam if SQLAlchemy is ever wanted). Keep the DDL portable. |
| D2 | The web UI will be server-rendered Jinja + HTMX (Phase 3). The two JSON routes (`/plans/generate`, `/activities/upload`) are stopgaps. Fold them into the HTML routes or delete them, and drop the `routes/<x>/{model,route,service}.py` three-file pattern. |
| D3 | The product is "Coachal" and the import package is `runcoach`. `*.egg-info` is gitignored. |
| D4 | **An unsafe plan never reaches the DB.** Generators return `violations`. `plan generate --save` exits 1 with violations, and `sync.replan_continuous_if_due` raises instead of writing. Every new save path (Phase 3 `/plan/save`, adaptation re-schedules) must do the same. |
| D5 | GPX stays on stdlib XML, not `gpxpy`. Still to add: the GPX 1.0 namespace and a SHA-256 `external_id`. |
| D6 | Goal distances are named: `5k`, `10k`, `half`, `marathon` (`PlanInput.distance`, CLI `--distance`, web `distance`). |
| D7 | No `clock.py`. Time is always a parameter, which keeps `engine/` pure. |
| D8 | `AGENTS.md` is the human's own "lazy senior developer" rules. Follow it. |
| D9 | The extra config keys the earlier agent added are fine (rule 6). They are reviewed with the rest of the defaults. |

## State per phase

Legend: ✅ done and matches the plan · 🟡 partial · ❌ not started.

**Phase 0** 🟡. T1–T3 ✅ (scaffold, layered config, sqlite + `.sql` migrations), T4 🟡 (`/healthz`
reports the real schema version; no Jinja, templates, static files or htmx yet), T5 ❌ PWA,
T6 ❌ `deploy/` units, T7 ❌ (human: Pi bring-up), T8 🟡 (`docs/perf.md` has only a Windows reading).

**Phase 1** ✅ apart from the human gate (read a printed half-marathon plan:
`python -m runcoach plan generate --distance half --date 2027-03-14 --vdot 49.8 --runs 4 --current-km 30`).
- `config.py`: `SchedulingConfig` now types **the whole config** (`extra="forbid"`, so unknown
  sections or keys raise). New sections: `zones`, `rpe`, `blocks`, `templates`,
  `race_min_weeks`, `min_peak_weekly_km`, `taper_factors`, `peak_weekly_km`.
  `load_config()` still returns the mutable `ConfigNode`, which tests edit in place. Engine code
  calls `load_scheduling_config(cfg)` to get the typed view.
- `engine/zones.py` reads `cfg.zones`. `engine/progression.py` has `split_blocks` (base, build,
  peak, taper), `weekly_targets` (capped growth; cut-backs don't advance the level; per-distance
  peak cap; taper) and `is_cutback_week`.
- `engine/templates.py`: `build_session(kind, WeekContext, zones, cfg)` makes real `Step` lists
  with pace ranges for easy, recovery, long, tempo (15→40 min of threshold work),
  intervals (3–6 × 3–5 min) and strides. `quality_minutes()` gives a session's length at a
  given block progress.
- `engine/plan.py`: the plan starts the Monday on or after `today` and runs through race week,
  with no long run in race week and nothing on or after race day. Roles come from the plan's
  table, and tempo and intervals alternate. Each quality session is capped at
  `templates.max_quality_share` of weekly time (below the shortest template it becomes an easy
  run). Weekly km follows what was actually composed, so a capped week doesn't leave later
  weeks needing a jump. Each week is composed twice: first runs alone with no floor (to remove
  the generator's own overshoot), then with football and climbing under the 40% floor. Taper
  weeks compose with growth 1.0. `InfeasibleGoal` covers too few weeks and growth above the cap,
  each with a suggested date.
- `engine/safety.py` `check_plan` now also checks the weekly leg-load rise (against the last
  week that wasn't a dip), long-run share of run time (weeks with 3+ runs only), pace order and taper above peak.
  `plan_start` excludes a partial first week. `exempt_load_weeks` covers return ramps and
  weeks where the scheduler dropped sessions. `cfg` is required.
- Tests: per-rule bad plans, templates, progression, the P1-T9 Hypothesis property test, and the
  golden snapshot.

**Phase 2** ❌ / 🟡. This is the next major work (Step 2 below).

**Phase 2b** ✅ mostly: T1, T3–T7 done. 🟡 T2 (`commitment add` has no validation, unlike
`event add`), 🟡 T8, ❌ T9 (optional calendar write), ❌ T10 (calendar step in `sync`).
This pass added two soft scheduler costs, `scheduler.same_day_session_cost` and
`adjacent_run_day_cost`, because runs were bunched on Mon/Tue.

**Phase 2c** ✅ mostly. Continuous sessions now use the templates (steps and paces), a
time-trial week also gets easy runs, and violations are returned separately. 🟡 T9: see the
Monday bug below.

**Phases 3, 4 and 5** ❌.

## Known bugs and gaps (fix as part of the step that touches them)

1. **Scheduler speed (do first in Step 2b work).** `schedule_week`'s depth-first search runs
   in full whenever the strict 48 h hard-leg spacing is impossible. Example: 5–6 runs a week
   give two quality sessions, plus the long run plus football, which is four hard sessions.
   The search then runs the relaxed pass. Measured on the desktop: a 30-week marathon plan with
   6 runs, 2 climbs and football takes **about 107 s** (about 2–7 s per week; the pre-Phase-1
   code took 33 s for the same input, with one quality session). A 12-week plan with 4 runs
   takes 0.2 s, which meets the "under 1 s" success criterion. On a Pi expect about 5× slower.
   Ideas: a cheap infeasibility pre-check that skips the strict pass, tighter branch-and-bound
   (the current prune compares `drop_cost + soft_cost` sums while the objective is
   lexicographic, so it is both inexact and weak), or memoising candidates per day.
   The result must stay deterministic. It also slows the test suite: the default run takes
   about 3 minutes (93 tests), and the 200-example race-plan property run about 5.
2. `engine/replan.should_replan` returns True **on every sync on a Monday** (48 times with a
   30-minute timer). It should check that `last_replanned_at` is before this Monday.
3. `adapt/rules.py`: every `Change` has `session_id=None`, so `apply_changes` never edits a
   session. `missed_two_plus` counts every past session, not skipped ones. `ill_or_niggle`
   only rewrites a reason. `retest_suggested` is missing. The rules work on `PlannedSession`,
   which has no id. Add DB loaders that return `(id, PlannedSession)` or a `Session` model.
4. `ingest/matcher.py`: scores by distance and ±2 days, where the spec says duration and
   ±1 day. There is no config threshold. Ties between candidates aren't detected. It keys on
   `id()` of objects.
5. `ingest/calendar.busy_slots` returns tuples. Nothing converts cached `calendar_event` rows
   into the scheduler's `Busy`.
6. `compose_week` scales **every** step uniformly, including stride and interval rep lengths
   (for example "6 x 0:17" strides). Consider scaling only steady, warm-up and cool-down steps,
   then whole reps.
7. Fixed commitments (football) aren't stored as `session` rows when a plan is saved, so a week
   view won't show them. Decide with the human (store them, or render from `commitment`).
8. Within a single plan, the race-plan safety check counts only planned sessions. Activities
   aren't in the load yet (Phase 2).
9. The day-of penalty RPE cap (`penalty.day_of_max_rpe`) also applies to the following
   "easy" days. The spec only removes quality on those days. Low risk.

## Human decisions on Phase 1 details (2026-11-15)

- **Two-run weeks are exempt from the long-run-share rule.** `check_plan` skips the rule for
  any week with two or fewer runs, and both generators only apply the share cap at 3+ runs.
- **Accepted as defaults** (still unsourced, tune from use): `min_peak_weekly_km`
  (5k 3, 10k 6, half 12.7, marathon 25.3, which keeps the old "distance × 0.6" heuristic),
  `templates.max_quality_share` = 0.25, `min_easy_minutes` = 20, and the scheduler's
  `same_day_session_cost` = 3 / `adjacent_run_day_cost` = 1.
- **Accepted race-week rules:** no long run in race week, nothing on or after race day, and no
  "race" session emitted.
- The human will run the Phase 1 gate (read a printed half-marathon plan).

## Ordered work for the next sessions

**Step 2: Phase 2 ingestion and adaptation** (gate: the human uploads 3 real runs).
1. P2-T1 `ingest/fit.py` with `fitparse` (add the dependency). Needs a real FIT file from the
   human. Print its `sport` values before mapping them.
2. P2-T2 GPX: add the 1.0 namespace and the SHA-256 `external_id` (D5).
3. P2-T3 `insert_activity` load fallback: activity RPE, then matched session RPE, then a new
   `[default_rpe]` config section.
4. P2-T4 Rewrite the matcher to the spec (bug 4).
5. P2-T5 Rewrite the rules with real session ids (bug 3). Reuse `db.apply_changes`, which is
   already transactional and tested.
6. P2-T7 `adapt/heat.py` plus the Open-Meteo fetch, tested with `pytest-httpx`. Needs the
   human's latitude and longitude. Check Open-Meteo's current parameter names.
7. P2-T8 inbox ingest plus `runcoach ingest <file>`.
8. P2-T9 the `runcoach sync` CLI with per-step try/except and `kv` timestamps. Include the
   continuous re-plan (`sync.replan_continuous_if_due`, which already refuses unsafe plans)
   after fixing bug 2.

**Step 3: finish 2b and 2c in `sync`.** P2b-T10: fetch and cache (`ingest/calendar.py`,
`db.cache_calendar_events`), build `Busy` from cached rows (bug 5), hash the window into `kv`,
re-schedule the next 14 days' planned sessions, log a `calendar_change` adjustment. Show
`detect_commitments` proposals for confirmation (Phase 3 UI). Fix scheduler speed (bug 1)
before this, because `sync` runs on the Pi every 30 minutes.

**Step 4: Phase 0 remainder and Phase 3 frontend.** Jinja `base.html`, `app.css`, vendored
htmx with its version noted, manifest, icons, service worker (P0-T4/T5). Then the routes table
in the Implementation Plan, the origin-check middleware, gzip and cache headers. Then
`deploy/*.service`/`.timer` and the backup script (P0-T6). P0-T7 and phone checks belong to
the human. Record idle RSS in `docs/perf.md`.

**Step 5: Phase 4 Garmin.** Start with the P4-T1 spike on the human's Forerunner 245 and wait for
their confirmation. Then `WatchExporter`, the mapper, auth, the exporter with
`0003_garmin.sql`, the `.fit` fallback and the ICS feed.

**Step 6 (optional): Phase 5 LLM explainer.**

## Inputs blocked on the human

FIT and GPX samples from their watch · home latitude and longitude · football start time,
duration and RPE · weekday and weekend availability windows · Pi model and RAM · Strava yes or
no · Google Calendar write yes or no · a review of every number in `config/defaults.toml`.
