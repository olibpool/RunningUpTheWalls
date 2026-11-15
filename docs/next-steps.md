# Next steps: handover for the next Claude session

Last updated 2026-11-15 on branch `phase1-completion`. Every task that can be done without the
human or their hardware is done; what's left is blocked on their inputs (listed at the end) or
is optional. Read `data/Strategic Plan.md`, then `data/Implementation Plan.md`, then `AGENTS.md`,
then this file. Task IDs (P2-T4 etc.) refer to the Implementation Plan. Where this file and the
plans disagree, this file records a decision the human already made. Don't reopen it without
asking.

## How to work in this repo

- **Run the checks with the venv Python.** Windows Application Control blocks the
  `pytest`/`mypy` launchers that `uv run` spawns:
  `.venv/Scripts/python.exe -m pytest -q`, `.venv/Scripts/python.exe -m mypy src`,
  `uv run ruff check . && uv run ruff format --check .` (ruff works through uv). The default
  suite takes 15–60 s.
- `pytest --update-golden` rewrites `tests/fixtures/golden_half_plan.json`. Only run it after
  reading the diff and agreeing the plan change is intended.
- `HYPOTHESIS_PROFILE=ci` gives 200 property examples, `pi` gives 50, and the default gives 25.
- Commit once per task with the task ID in the message. Never bundle work into a "WIP" commit.
- Agent worktree isolation fails on this machine (a `c:` vs `C:` path mismatch). Create the
  worktree yourself in the scratchpad and set `PYTHONPATH=<worktree>/src` for ad-hoc scripts.

## Decisions already taken (don't reopen)

| # | Decision |
|---|---|
| D1 | **No Alembic or SQLAlchemy.** Migrations are numbered `migrations/NNNN_name.sql`, applied by `db.migrate()` through `PRAGMA user_version`, one transaction each. Never edit an applied file. All SQL stays in `db.py` behind model-in, model-out functions (the seam if SQLAlchemy is ever wanted). |
| D2 | Server-rendered Jinja + htmx, one router module per page group. No JSON API besides `/healthz` and the exports. |
| D3 | The product is "Coachal"; the import package is `runcoach`. |
| D4 | **An unsafe plan never reaches the DB.** Generators return `violations`; every save path (CLI `--save`, `/plan/save`, `/plan/continuous/save`, the weekly re-plan) refuses when there are any. Session moves are refused when they introduce a violation. |
| D5 | GPX is parsed with stdlib XML (1.0 and 1.1), with `external_id` = SHA-256 of the file. |
| D6 | Goal distances are named `5k`, `10k`, `half`, `marathon`. |
| D7 | No `clock.py`. Time is always a parameter, which keeps `engine/` pure. |
| D8 | `AGENTS.md` holds the human's "lazy senior developer" rules. Follow them. |
| D9 | Extra config keys are fine as long as every tunable stays in `config/defaults.toml`. |
| D10 | **Two-run weeks are exempt from the long-run-share rule** (safety and both generators). |
| D11 | Accepted unsourced defaults: `min_peak_weekly_km`, `templates.max_quality_share` = 0.25, `min_easy_minutes` = 20, `scheduler.same_day_session_cost` = 3, `adjacent_run_day_cost` = 1. Race week has no long run, nothing on or after race day, and no "race" session. |
| D12 | Backups use SQLite's online backup API (`runcoach backup`), not the `sqlite3` CLI, which Pi OS Lite lacks. |
| D13 | **Phase 4 was built before the P4-T1 watch check**, at the human's request. The spike (`scripts/garmin_spike.py`) is still the gate; record its results in `docs/garmin-notes.md` and fix the mapping if the watch shows steps wrongly. |
| D14 | Pushed workouts live in a `garmin_push` table (no foreign key), not `session.garmin_hash`, so a workout can still be removed after its session is deleted by a re-plan. A changed session is replaced by delete + upload (deleting a workout also clears its schedule). Past workouts stay on the Garmin calendar. |
| D15 | The human also asked for: Garmin activity and resting-HR pull, per-rep scoring against pace targets (`adapt/compliance.py`, feeding the retest suggestion both ways) and the plan wizard's weekly km taken from recorded runs. Coachal's own session-RPE load stays the load model; Garmin's training-load numbers are not used. |
| D16 | Coaching review (sports-science pass): zones match Daniels' tables (M 0.82, T 0.88); peak km is capped at `max_weekly_km_per_run` x runs a week and easy runs at 80% of the long run; base has threshold + strides only (no VO2max reps), peak leans to the race (intervals for 5K/10K, threshold for half, marathon-pace long-run finishes for the marathon); threshold work over 20 min becomes cruise intervals; T and I work are capped at 10% and 8% of weekly km (Daniels); trimming shortens whole minutes and drops whole reps instead of shrinking rep length; football counts as one of at most 3 hard leg days a week; race week is one sharpener, up to 3 short shakeouts, 2 easy days and the race itself, a `race` session with the VDOT-predicted pace; `min_peak_weekly_km` raised to novice-plan levels. |
| D17 | Follow-ups to D16, agreed with the human: continuous mode with no load ceiling stops growing at `max_weekly_km_per_run` x runs of mostly easy running (`continuous.ceiling_run_rpe`) plus a usual week's football and climbing, never below current load; marathon plans allow a 42% long-run share (`marathon_long_share` 34-40%); a check-in can flag fever or chest symptoms (`checkin.fever`, migration 0004), which drops every session for `illness.fever_rest_days` before the usual illness window and ramp; no run is planned more than `max_single_run_increase` (10%) above the longest run of the last 30 days, counting stored runs (wizard, CLI, continuous history) and the plan's earlier weeks. |

## State per phase

Legend: ✅ done · 🟡 partial · ⛔ blocked on the human · ❌ not started.

**Phase 0** ✅ except ⛔ P0-T7 (Pi bring-up, see `deploy/README.md`) and P0-T8 on a Pi
(`docs/perf.md` has Windows readings only). `deploy/` has the app service, a 30-minute
`sync.timer`, a 03:30 `backup.timer` (optional rsync off by default) and an env example.
`systemd-analyze verify` couldn't be run on Windows; the deploy guide runs it.

**Phase 1** ✅. Gate ⛔: the human reads a printed half-marathon plan.

**Phase 2** ✅ except P2-T10 (Strava: optional, off). P2-T1 (`ingest/fit.py`, FIT and
Garmin's zipped originals, with laps) is tested only against FIT files written by
`integrations/fit.py`; ⛔ check it against a real watch file and its `sport` values.
- `ingest/inbox.py`: `ingest_file` (CLI `runcoach ingest`, web upload, inbox) is idempotent;
  load is estimated from `[default_rpe]` until matched, then from the session's planned RPE.
- `ingest/matcher.py`: spec scoring (day offset 0/0.5 plus relative duration gap under
  `[matching] max_score`); ties stay unmatched for the human.
- `adapt/rules.py` (pure; works on stored `Session`s): `missed_once`, `missed_two_plus`,
  `ill_or_niggle` (by-feel window, then a 70/85% ramp), `event_constraints` (drinks, holidays
  and active trips logged after a plan was made, plus the race-plan holiday ramp) and
  `retest_suggested` (a kv flag; VDOT never changes without the human). The rules run every
  sync, so load-scaling rules skip sessions the adjustment log says they already touched, and
  plans record the events they were generated with (`config_snapshot.event_ids`) so a ramp is
  never applied twice.
- `adapt/heat.py` with the Open-Meteo fetch (`integrations/weather.py`). It stays off until
  `[weather]` has the human's location.
- `runcoach sync`: inbox, calendar, forecast, matching, adaptation and the continuous re-plan,
  each isolated, with `kv` `sync.<step>.last_ok` and `sync.last_report`.
- Gate ⛔: the human uploads 3 real runs.

**Phase 2b** ✅ except ❌ P2b-T9 (optional Google Calendar write). The calendar step re-schedules
only weeks where a planned session now clashes with a busy event (`adapt/reschedule.py`,
rule `calendar_change`). The iCal URL is a secret: it's never shown or logged, and fetch
errors report only the error type. The scheduler speed-up is merged (`_cost_bound`, fewer
duplicate starts; output unchanged). Gate ⛔ needs the human's calendar.

**Phase 2c** ✅. Race plans now honour drinks, holidays and active trips both at generation and
after saving. The Monday re-plan bug is fixed: the re-plan runs once from Monday, or when the
interval has passed.

**Phase 3** ✅ (all of P3-T1 to T10). Pages: `/week`, `/session/{id}` (done, skip, move with
`check_plan`), `/plan`, `/plan/new` (race), `/plan/continuous`, `/activities` (upload,
unmatched list, assign), `/checkin` (with the resting-HR hint, offered but never applied),
`/events` (fixed commitments, drinks, holidays, calendar proposals to confirm), `/settings`
(athlete profile, time-trial VDOT update, integration status, background sync with htmx
polling) and the PWA (manifest, icons, `/sw.js`; offline falls back to the last week page).
Gate ⛔ is on the human's phone. Still to verify there: the origin check behind
`tailscale serve` (it prefers `X-Forwarded-Host`) and the offline shell.

**Docs and home page** ✅: `/` is a home dashboard (today, plan headline, attention list, first-run checklist), `/guide` is the in-app user guide, and `docs/user-guide.md` covers setup, config and CLI. Update all three when a feature lands.

**Phase 4** ✅ in code, ⛔ unverified on the watch (D13). Built against `garminconnect` 0.3.20's
source:
- `integrations/garmin.py`: `login`/`connect` (token-only, in `[garmin] token_dir`; one login
  attempt per sync, shared by pull and push), `pull_activities` (last `pull_days_back` days,
  original FIT, skips runs already stored from any source), `pull_resting_hr` (fills check-ins,
  never over your own value), `to_garmin_workout` (P4-T3: steps numbered across the workout,
  repeat children with `childStepId`, pace targets in m/s with the slower pace first) and
  `GarminExporter.sync_window` (P4-T5/T9: hash diff, per-call saves, removal of runs no longer
  planned).
- `runcoach sync` steps: `garmin` (pull, before matching) and `watch` (push, last).
  `runcoach garmin login|status` (P4-T4).
- P4-T7: `integrations/fit.py` writes FIT workout files itself (no `fit-tool`), served at
  `/session/{id}/workout.fit`; round-trip tested with fitparse.
- UI (P4-T6): Settings shows Garmin and watch status (the existing "Run sync now" button covers
  push and pull), and the session page shows "On your watch", the `.fit` link and "How it went".
- ⛔ Still to confirm with the spike: the 245 shows steps, repeats and pace ranges; the name
  limit (`[garmin] workout_name_max` = 32 is a guess); the `workoutScheduleId` response key;
  whether laps pair 1:1 with executed steps (`adapt/compliance.py` pairs by count, see its
  shortcut comment); and the FIT `.zip` layout of Garmin originals.

**Phase 5** ❌: optional, and per the plan only after the Phase 0–4 gates pass.

## Known gaps

1. Scheduler: a dense 30-week plan (6 runs, 2 climbs, football) took about 11 s on this
   desktop after the speed-up (about 2 s unloaded, by the agent's estimate). Remaining ideas: a
   hard-leg count pre-check that skips the strict pass, and adjacent-run costs in the bound.
2. `compose_week` scales every step uniformly, including stride and rep lengths (e.g. "6 x 0:17").
   Consider scaling only steady, warm-up and cool-down steps, then whole reps.
   `engine/templates.scale_session` repeats the same step scaling as the scheduler's private
   `_scale_step`; merge them when touching either.
3. The day-of penalty RPE cap also applies to the following easy days (the spec only removes
   quality there). Low risk.
4. The calendar re-schedule passes earlier sessions of the week as `prior_tail`, so same-day
   and rest-day rules don't see them; only hard-leg spacing does.
5. Week view: fixed commitments render live from `commitment` rather than as `session` rows.
6. Availability, location and the iCal address are edited in `config/local.toml` or env vars,
   not in the Settings page.

## Web UI conventions (keep to these so pages don't need rewriting)

- One router module per page group in `web/routes/`, registered in `web/routes/__init__.py`;
  top-level pages go in `NAV` in `web/deps.py`.
- Partials start with `_` and are what htmx swaps. Every form works without JavaScript, plus
  `hx-*` for in-place swaps; `is_htmx()` picks a fragment, and `redirect()` answers a POST.
- Forms are parsed with `parse_form(Model, await request.form())`. Commitment validation lives
  in `domain.models.commitment_problem`, shared with the CLI.
- Routes take `Depends(get_conn)` and `Depends(get_cfg)`; all SQL stays in `db.py`. Changes to
  sessions always go through `db.apply_changes` so each one lands with its adjustment row.
- After anything that should change the plan (an event, a check-in, an upload), call
  `sync.adapt_plan` (and `sync.match_activities` after an upload) so the effect is immediate.
- `runcoach/display.py` holds formatting shared with the CLI, exposed as Jinja filters.
- Tests build `create_app(db_path=tmp, cfg=...)` per test.

## Inputs blocked on the human

- The Garmin spike (`scripts/garmin_spike.py`): the activity list, a FIT file, and what the
  Forerunner 245 shows for the pushed workout
- Home latitude and longitude, then set `[weather] enabled`
- The Google Calendar secret iCal address, in `RUNCOACH_GCAL__ICS_URL`
- Football details, entered in the app
- Weekday and weekend availability windows, in `config/local.toml`
- Pi model and RAM
- Strava, and Google Calendar write access: both optional, decide yes or no
- A review of every number in `config/defaults.toml`
