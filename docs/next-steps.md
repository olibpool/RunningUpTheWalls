# Next steps: handover for the next Claude session

Last updated 2026-11-15 on branch `phase1-completion` (Phase 1 plus the basic Phase 3 UI). Read `data/Strategic Plan.md`, then
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
  The 200-example race-plan run takes a few minutes (see "Scheduler speed" below).
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

**Phase 0** 🟡. T1–T3 ✅ (scaffold, layered config, sqlite + `.sql` migrations), T4 ✅ (Jinja, `app.css`,
vendored htmx 2.0.4 with `static/vendor/VERSIONS.txt`, `/healthz` with the real schema version),
T5 ❌ PWA (manifest, icons, service worker),
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

**Phase 3** 🟡: the basic UI is in, moved ahead of Phase 2 at the human's request so they
can start using the plan. See "Web UI conventions" below.
- ✅ P3-T1 layout and nav. ✅ P3-T2 week view (`/week?start=`), with fixed commitments and
  event notes rendered live from `commitment` (never copied into `session`).
- 🟡 P3-T3: session detail, Done (logs a `manual` activity with optional RPE and minutes,
  plus a `manual_done` adjustment) and Skip. **Move is not built yet**; it must validate with
  `check_plan` and show violations.
- ✅ P3-T6 events (fixed commitment form; drinks; holiday as full break, travel or active;
  delete). ❌ calendar proposals to confirm (needs P2b-T10).
- 🟡 P3-T7 plan wizard: race mode only (race time or VDOT); preview; save refuses violations
  (D4). Continuous mode needs run history first (Phase 2).
- 🟡 P3-T9: the origin check and gzip are done. One-day cache headers for static files aren't
  yet. **Verify the origin check behind `tailscale serve`**, which may rewrite `Host`; the
  middleware already prefers `X-Forwarded-Host`.
- ❌ P3-T4 activities (upload page, unmatched list; `/activities/upload` is still the old JSON
  parse-only stopgap), ❌ P3-T5 check-in, ❌ P3-T8 settings and sync runner, ❌ P3-T10 offline.

**Phases 4 and 5** ❌.

## Known bugs and gaps (fix as part of the step that touches them)

1. **Scheduler speed** 🟡 (P2b-T6, mostly fixed). The slow part was proving a schedule optimal,
   not finding it: in dense weeks (5–6 runs give two quality sessions, plus the long run and
   football, so the strict 48 h pass fails and the relaxed pass runs) the first leaf is already
   optimal, then thousands of nodes are spent ruling out cheaper ones. Two changes, same output:
   - `_cost_bound` prunes a branch when its soft cost plus a lower bound for the remaining
     sessions exceeds the best. Each remaining session costs at least its cheapest day now
     (`_day_cost`, the time-independent part of `_soft_cost`; costs only grow as placements
     are added), and only one session per day without a flexible session escapes the same-day
     cost. Any leaf it cuts would have been pruned by the old sum check, so results are
     unchanged (a unit test compares against the search without the bound).
   - `_candidate_starts` adds the ±48 h hard-leg and limit-climb offsets only for sessions
     those rules apply to. Easy runs and volume climbs had copies on every day.
   Measured side by side on a loaded desktop (old → new; this desktop ran ~6× slower than when
   the 107 s figure was taken): 30-week marathon, 6 runs, 2 climbs, football 634 s → 12 s
   (about 2 s unloaded); 30 weeks, 6 runs, no climbs 11.7 s → 1.9 s; 12 weeks, 4 runs
   0.16 → 0.10 s, with climbs and football 0.24 → 0.11 s. Default suite (fixed Hypothesis
   seed) 61 s → 17 s; the 200-example race-plan run took 157 s on the loaded machine.
   Left: the strict pass still searches about 400 nodes a week before the relaxed pass in
   four-hard-session weeks (a hard-leg count pre-check could skip it), and the bound ignores
   adjacent-run costs between unplaced sessions. On a Pi expect a dense 30-week plan in ~10 s.
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
7. **Race plans ignore drinks nights and holidays.** Only the continuous generator turns
   penalty/block/credit events into `DayConstraint`s. `generate_race_plan_result` should pass
   `day_constraints(expand_commitments(...))` to `schedule_week` and `check_plan` (merged with
   its race-day blocks), apply `ramp_for_block` after blocks, and exempt the return weeks, as
   continuous mode does. Until then the week view shows a banner saying so.
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

## Web UI conventions (keep to these so pages don't need rewriting)

- One router module per page group in `web/routes/` (`week`, `sessions`, `plans`, `events`,
  `health`, `activities`), registered in `web/routes/__init__.py`. Add a top-level page to
  `NAV` in `web/deps.py`.
- Server-rendered Jinja (`web/templates/`). Partials start with `_` and are what htmx swaps
  (`_session_card.html`, `_day.html`, `_messages.html`, `_week_table.html`, `_steps.html`).
  Every form works without JavaScript: `method`/`action` for plain posts, plus `hx-*` for
  in-place swaps. Handlers use `is_htmx()` to return a fragment, and `redirect()` (a 303, or
  `HX-Redirect` for htmx) after a POST.
- Forms are parsed with `parse_form(Model, await request.form())`, which turns pydantic errors
  into messages for `_messages.html`. Validation of commitments lives in
  `domain.models.commitment_problem`, shared with the CLI.
- Routes get the DB through `Depends(get_conn)` (one connection per request) and config
  through `Depends(get_cfg)`. All SQL stays in `db.py` (D1). Read helpers added for the UI:
  `active_plan`, `plan_sessions`, `get_session`, `session_adjustments`, and
  `list_commitments`, which now returns `Commitment` models. Saving a plan marks the
  previous active plan `complete`.
- Formatting shared with the CLI is in `runcoach/display.py`, exposed as Jinja filters
  (`pace`, `step_pace`, `main_pace`, `structure`, `clock`, `km`).
- Tests: `tests/unit/test_web.py` builds `create_app(db_path=tmp, cfg=...)` per test.

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

**Step 4: rest of Phase 3 and Phase 0.** Session move with `check_plan` validation (P3-T3),
the activities page (P3-T4, after Step 2's ingest), check-in (P3-T5), settings and the sync
runner (P3-T8), static cache headers (P3-T9), and calendar proposals on `/events`. Then the PWA
manifest, icons and service worker (P0-T5, P3-T10), and `deploy/*.service`/`.timer` plus the
backup script (P0-T6). P0-T7 and phone checks belong to the human. Record idle RSS in
`docs/perf.md`.

**Step 5: Phase 4 Garmin.** Start with the P4-T1 spike on the human's Forerunner 245 and wait for
their confirmation. Then `WatchExporter`, the mapper, auth, the exporter with
`0003_garmin.sql`, the `.fit` fallback and the ICS feed.

**Step 6 (optional): Phase 5 LLM explainer.**

## Inputs blocked on the human

FIT and GPX samples from their watch · home latitude and longitude · football start time,
duration and RPE · weekday and weekend availability windows · Pi model and RAM · Strava yes or
no · Google Calendar write yes or no · a review of every number in `config/defaults.toml`.
