# Self-Hosted Running Coach: Build Plan

Nov 10, 2026 · @Oli

## Goals and scope

v1 generates a personalised training plan for one athlete, built around running with football and climbing fitted in, adapts it weekly from completed runs, and is reachable from desktop and phone over a private network. Running cost beyond the Pi and electricity is zero.

**In scope**

- Plans for 5K to marathon from a recent race time, days per week and a target date
- Weekly adaptation from planned vs. completed sessions, with a goal-free continuous improvement mode as an alternative to race plans
- Football as a fixed weekly commitment (Tuesdays), climbing sessions suggested into the best free days, and drinks nights and holidays as events that change the plan
- Google Calendar integration: read busy times and commitments, optionally write sessions to a Training calendar
- Heat adjustment using free weather data
- Mobile-friendly web app installable on the phone home screen
- Push of structured workouts to your Garmin Connect calendar so they sync to the watch, with .fit and calendar export as fallbacks

**Out of scope for v1**

- Multi-user accounts, payments, native iOS or Android apps
- Live in-run audio coaching
- Official Garmin or Strava partner integrations

**Success criteria**

- A full 12-week plan generates in under 1 second from four inputs
- Every generated plan passes the safety tests in Phase 1
- You use it for a real training block and prefer it to a static spreadsheet

## Architecture and tech stack

One Python process, one SQLite file and server-rendered pages keep the whole app near 100 MB of RAM on a Pi 4 (approximate, measure it). There is no Node runtime, no JavaScript build step and no database server.

| Layer | Choice | Why it suits a Pi | Avoid |
| --- | --- | --- | --- |
| OS | Raspberry Pi OS Lite, 64-bit | No desktop overhead; ARM64 wheels available | Desktop image |
| Backend | Python 3.12, FastAPI, Uvicorn with 1 worker | Async, small footprint, fits your existing skills | Gunicorn with many workers |
| Frontend | Jinja2 templates + HTMX + \~50 lines of vanilla JS | Pages render on the server; the phone loads a few KB | React, Vite, npm at build or run time |
| Database | SQLite in WAL mode, stdlib `sqlite3` + Pydantic models | Zero idle cost; backup is one file | Postgres, Redis, an ORM |
| Maths | Python stdlib `math` | Pace and load formulas need no arrays | numpy, pandas, scipy |
| Background jobs | systemd timer running a CLI sync command | Nothing resident between runs | APScheduler, Celery |
| Workout export | python-garminconnect for Garmin push; FIT writer and ICS as fallbacks | No native build on ARM | Heavy FIT SDKs |
| Weather | Open-Meteo over `httpx` | Free, no API key | Paid weather APIs |
| Process management | systemd service; Docker optional | Starts on boot, auto-restarts | Docker if RAM is under 2 GB |
| Remote access | Tailscale + `tailscale serve` for HTTPS | Private, no open ports; HTTPS enables PWA install | Port forwarding |
| Storage | SSD, or an endurance SD card | SQLite writes wear cheap cards | Standard SD cards |
| Optional LLM | Ollama on your desktop, called over Tailscale | Keeps inference off the Pi | Models on the Pi |

**How the parts connect**

1. Phone and desktop browsers reach the Pi at its Tailscale HTTPS address.
2. FastAPI serves HTML pages and small HTMX fragments, and reads and writes SQLite.
3. The planning engine is a pure-Python module with no I/O, so it is easy to unit test.
4. A systemd timer runs `sync` every 30 minutes to pull new runs and weather, then re-evaluates the plan.
5. The optional LLM adapter calls your desktop and falls back to templated text when it is offline.

**Data model (SQLite)**

- `athlete`: race results, paces, days available, weekly climbing target, injury flags
- `plan` and `session`: planned sessions for any sport, with target pace, distance, type and load score
- `activity`: completed sessions imported from FIT/GPX files, Strava or Garmin, or logged by hand
- `adjustment`: a log of every automatic change and its reason
- `commitment`: events with a kind (fixed, readiness penalty, availability block or load credit), dates or weekday, time, duration and intensity
- `calendar_event`: cached Google Calendar events used to find busy slots

## Phase 0: Pi hosting skeleton

Goal: a "hello world" page loads on your phone over Tailscale and survives a reboot. Estimated effort: one evening.

1. Flash Raspberry Pi OS Lite (64-bit), enable SSH, and boot from an SSD or endurance SD card.
2. Install Tailscale on the Pi, desktop and phone; confirm all three can reach each other.
3. Create a Python virtual environment with `uv` or `venv` and install FastAPI, Uvicorn, Jinja2 and httpx.
4. Add a systemd unit for Uvicorn (1 worker, bound to localhost) with `Restart=always`.
5. Run `tailscale serve` to publish it over HTTPS at your tailnet address.
6. Add a nightly systemd timer that runs `sqlite3 app.db ".backup"` and copies the result to your desktop.
7. Add a PWA manifest and icon, then install it to the phone home screen.

**Gate to move on:** reboot the Pi and confirm the page still loads on the phone with no manual steps.

## Phase 1: Planning engine

Goal: a pure-Python module that turns four inputs into a safe, periodised plan. It does no I/O and is fully unit tested. Estimated effort: 2 to 4 evenings.

**Inputs:** a recent race time (or time trial), goal distance, target date, and days available per week.

**Components**

- **Pace zones:** Daniels VDOT from the race time gives easy, marathon, threshold, interval and repetition paces. Riegel handles distance equivalence.
- **Periodisation:** base, build, peak and taper blocks sized to the weeks available.
- **Progression:** weekly volume rises gradually, with a cut-back week roughly every third or fourth week. Caps live in a config file, not in code paths.
- **Session templates:** easy, long, tempo, intervals and strides, each with structured steps (warm-up, repeats, cool-down).
- **Taper:** volume drops over the final 1 to 3 weeks depending on the goal distance.

**Safety tests (the part that matters most)**

- Weekly volume never rises by more than the configured cap over the previous week
- The longest run never exceeds a fixed share of weekly volume
- Hard sessions are never scheduled on consecutive days
- Every plan includes at least one full rest day
- Paces are monotonic: easy is slower than threshold, which is slower than interval

Use `pytest` with property-based tests (Hypothesis) to run these checks across thousands of random inputs. A plan that fails any check should never reach the database.

**Gate to move on:** all safety tests pass, and a generated half-marathon plan looks sensible when you read it as a runner.

## Phase 2: Data ingestion and adaptation

Goal: completed runs flow in automatically and the plan adjusts by explicit rules, each logged with a reason. Estimated effort: 3 to 5 evenings.

**Ingestion options, in order of effort**

1. **Manual FIT/GPX upload:** a form that parses a file from your watch. No third-party API, works from day one, and the best starting point.
2. **Strava API:** OAuth once, then poll from the systemd timer. Check Strava's current API terms on AI use of your data before relying on it.
3. **Garmin Connect (personal API or unofficial library):** Garmin's personal API key is free and self-serve but pull-only, and it is unclear what its activity files include. The unofficial library that pushes workouts can also pull runs, so use it once Garmin push is working.

**Rule-based adaptation** (each rule writes a row to `adjustment` with its reason)

| Trigger | Response |
| --- | --- |
| Session missed once | Skip it; do not cram it into the next day |
| Two or more sessions missed in a week | Repeat the previous week's volume |
| Illness or niggle flagged | Remove pace targets for 3 to 14 days, then ramp back |
| Paces consistently faster than target | Offer a re-test and update VDOT |
| Hot forecast | Slow target paces using a heat table, or move the session earlier in the day |

**Heat adjustment:** pull the forecast from Open-Meteo and apply a pace penalty by temperature and dew point. Start with a conservative lookup table you can tune from your own data.

**Gate to move on:** upload three real runs, confirm they match to planned sessions, and check the adjustment log explains every change.

## Phase 2b: Calendar and multi-sport scheduling

Goal: the planner knows football is fixed on Tuesdays, reads your Google Calendar, and places runs and climbing sessions on the days that suit them. Estimated effort: 4 to 6 evenings.

**Calendar integration**

Google Calendar is separate from Gmail, so the app needs calendar access only and never touches your mail.

| Route | Access | Trade-off |
| --- | --- | --- |
| Private iCal address from the calendar's settings | Read-only | No OAuth and the least setup. Check that the option is still offered in your account settings |
| Google Calendar API with OAuth | Read and write | Needed to write sessions to a Training calendar. A consent screen left in "Testing" status expires refresh tokens after 7 days, so set it to "In production" for personal use and accept the unverified-app warning |

Start with the iCal read. Add the API only if you want sessions written back, since the Garmin push already puts them on your watch.

**Fixed commitments**

A small `commitment` table (sport, weekday, start time, duration, intensity) is the source of truth for football. Calendar events whose title matches a keyword confirm or override it. A mistyped event title should never silently drop football from your weekly load.

**Load model**

Every session, planned or completed, gets a load score of session-RPE: effort from 1 to 10 multiplied by minutes. Football research uses the same measure. Load is tracked in two channels so that sports that tax different tissues don't count against each other.

| Session | Leg load | Finger and upper-body load | Who places it |
| --- | --- | --- | --- |
| Football | High | None | You (fixed) |
| Long run | High | None | Planner |
| Tempo or interval run | High | None | Planner |
| Easy run | Low to medium | None | Planner |
| Climbing: limit or power | Low | High | Planner suggests |
| Climbing: volume or technique | Low | Medium | Planner suggests |

**Scheduling rules (configurable defaults)**

These are heuristics drawn from limited evidence, not proven thresholds. Keep them in a config file and tune them from how you feel.

1. Fixed commitments are placed first and never moved.
2. Football counts as a hard leg session. Keep at least 48 hours between hard leg sessions (football, tempo or interval run, long run), and relax this to 24 hours only when a week cannot otherwise fit.
3. The day after football is a good slot for climbing, because the muscle groups barely overlap. The planner flags it as lower confidence if you report feeling flat.
4. Climbing targets 2 to 3 sessions a week, editable. Keep limit or power sessions at least 48 hours apart.
5. When two sessions share a day, leave at least 6 hours between them. Prefer separate days.
6. The weekly progression cap from Phase 1 applies to combined leg load, not running distance alone.
7. When the calendar leaves no free slot, drop sessions in this order: easy runs, optional climbing, then quality runs. The long run and football stay.

Every climbing suggestion shows its reason, for example "Wednesday: 24 hours after football, no hard run within 48 hours".

**Example week the planner might produce**

| Day | Session | Reason |
| --- | --- | --- |
| Monday | Rest or easy run | Recovery after the long run |
| Tuesday | Football (fixed) | Hard leg day |
| Wednesday | Climbing: power or technique | Low leg overlap with football |
| Thursday | Tempo or interval run | 48 hours after football |
| Friday | Climbing: volume | Low leg load, 48 hours after the last limit session |
| Saturday | Easy run or optional climbing | Keeps legs fresh for Sunday |
| Sunday | Long run | 72 hours after football |

The commitments form is built in Phase 3.

**Gate to move on:** with football fixed on Tuesdays, generate four weeks of plans that obey every rule and show a reason for each climbing slot. Then move a calendar event and confirm the affected session moves.

Sources: [Google OAuth refresh token rules](https://developers.google.com/health/setup), [Lattice on concurrent training](https://latticetraining.com/lattice-newsletter-issue-3/), [recovery time between concurrent sessions](https://pubmed.ncbi.nlm.nih.gov/25546450/), [review of concurrent training in football](https://pubmed.ncbi.nlm.nih.gov/39652049/), checked November 2026.

## Phase 2c: Continuous improvement mode, drinking days and holidays

Goal: a mode switch lets the app run with no race, and typed events such as drinks nights and holidays change the plan. Estimated effort: 5 to 7 evenings once Phase 2b works.

**Continuous improvement mode**

A race plan works backwards from a date: base, build, peak, taper. With no date, the engine uses a forward rule instead. It estimates your recent load as a rolling average, sets next week slightly above it, and drops back every third or fourth week.

- **Horizon:** re-plan every Monday for the next 4 weeks, with no end date.
- **Weekly target:** next week's leg load is the recent average plus a small configurable increase. A cut-back week comes every 3rd or 4th week.
- **Block rotation:** 4-week blocks that alternate emphasis between aerobic volume, threshold and speed. This replaces periodisation without a finish line.
- **Ceiling:** you set a maximum weekly load and running time. At the ceiling, the plan shifts from more volume to better quality.
- **Mini-goal:** every 8 to 12 weeks, a 5K time trial or similar test updates your paces. It is a checkpoint, not a race.

The usual guardrail is the acute-to-chronic load ratio, which compares the last 7 days with the previous 3 to 6 weeks and treats roughly 0.8 to 1.3 as the safe band. Its validity as an injury predictor is contested, so show it as a soft warning that nudges the plan. The hard limits stay as the Phase 1 progression caps.

Free tools already cover goal-free running, including Runna's non-race plans, Strava Instant Workouts and Garmin Daily Suggested Workouts. The value here is scheduling around football and climbing.

**Typed events**

Every event gets a kind. This extends the `commitment` table from Phase 2b, with no new tables.

| Kind | Example | Effect on the plan |
| --- | --- | --- |
| Fixed | Football on Tuesdays | Placed first, never moved, counts as a hard leg session |
| Readiness penalty | A drinks night | Lightens that day's session, removes quality running and limit climbing the next day |
| Availability block | Holiday or travel | Removes sessions for those dates, then applies a return ramp |
| Load credit | Hiking or skiing holiday | Counts as leg load, and no running is stacked on top |

Events enter by a quick log in the app or a keyword in a Google Calendar title (for example "drinks" or "holiday"). Multi-day all-day events are proposed as holidays for you to confirm.

**Drinking days**

Treat a drinking night as a temporary readiness penalty, not a moral judgement. A large wearable study found that even low alcohol intake raised overnight resting heart rate, lowered HRV, shortened sleep and reduced next-day activity. The effects were smaller when people drank earlier, slept longer afterwards and kept exercise light. The data is observational and does not prove causation.

The mechanism that matters for planning is indirect. A hard session's adaptation happens during the following sleep, and alcohol degrades that sleep. So the penalty falls on the next day's quality, not on the night itself.

1. A drinks night planned in advance moves or downgrades quality sessions in the next 24 hours to easy.
2. A night logged afterwards downgrades the next session the same way.
3. A session on the same day is capped at easy effort, with no key session the morning after.
4. If your morning resting heart rate is well above your baseline, the app suggests extending the penalty by a day.

The 24-hour default and the resting heart rate trigger are heuristics to tune from your own data. One vendor reports that some athletes stay below baseline for two days, but that is unreviewed company data.

**Holidays**

Detraining is slower than most people fear. Measurable VO2max loss starts at about day 10 without training, and reaches roughly 4 to 6% after two weeks. A 6% decline can be made up in one or two weeks of solid training. Cutting volume while keeping intensity preserves more than stopping, and one summary of the research says a third of your usual frequency and duration can hold VO2max.

| Holiday type | Plan during the break | Return |
| --- | --- | --- |
| Full break | Nothing scheduled | Ramp back at reduced load, with no quality sessions in the first week |
| Travel with running | 2 to 3 short runs of about a third of normal duration, one with some intensity | Resume normally |
| Active holiday | Hiking or skiing logged as load | Resume normally |

Starting defaults for the return ramp, not sourced thresholds:

- Up to 10 days off: resume at about 80% of prior load.
- 10 to 28 days off: a 3-week ramp of roughly 60%, 80%, then 100%.
- Over 4 weeks off: treat it as a return-to-running plan.

**Gate to move on:** generate a 6-week continuous plan containing one drinks night and a 10-day holiday. Confirm that holiday dates have no sessions, the ramp applies, the day after drinks is downgraded, and every Phase 1 and Phase 2b safety test still passes. Add property-based tests that place random events and check the caps hold.

Sources: [Alcohol, sleep and next-day activity study](https://doi.org/10.1371/journal.pdig.0001284), [Runners Connect on losing running fitness](https://runnersconnect.net/losing-running-fitness/), [TrainRight on missed training](https://trainright.com/prevent-missed-training-from-hurting-performance/), [Critique of training load ratios](https://nata.kglmeridian.com/view/journals/attr/55/9/article-p893.xml), [Strava Instant Workouts vs Garmin](https://the5krunner.com/2026/01/09/strava-instant-workouts-vs-garmin-dsw/), checked November 2026.

## Phases 3 and 4: Frontend and watch export

**Phase 3: Frontend (2 to 3 evenings).** Server-rendered pages with HTMX, styled with a single small CSS file. No client-side framework.

- Week view: the seven sessions with target pace, distance and status
- Session detail: the structured steps, plus a "mark done" or "upload run" action
- Check-in form: how it felt, any niggle, illness toggle (feeds the adaptation rules)
- Plan setup: the four inputs, with a preview before saving
- PWA basics: manifest, icon and a service worker that caches the shell

**Phase 4: Garmin push and export (2 to 4 evenings).**

| Route | Effort | Notes |
| --- | --- | --- |
| Garmin Connect via the unofficial `garminconnect` library | Medium | Uploads a structured workout and schedules it on your Connect calendar; the phone app syncs it to the watch |
| Official Garmin Training API | Not viable | Business tiers only; a personal API key cannot push workouts |
| `.fit` workout file copied by USB | Low | Reliable manual fallback |
| ICS calendar feed | Low | Shows sessions in any calendar app, without workout steps |

**How the Garmin push works**

1. The planning engine emits each session as structured steps (warm-up, repeats, cool-down) with pace targets.
2. A `WatchExporter` interface maps those steps to Garmin's workout format, uploads the workout and schedules it on the session's date.
3. The Garmin Connect phone app syncs the calendar to your watch, where the workout appears.
4. When the adaptation engine changes a session, it unschedules the old workout and uploads the new one. The `session` table stores the Garmin workout and schedule ids.
5. The first login, including any MFA prompt, runs once on your desktop. Copy the saved token file to the Pi, keep it readable only by your user, and never commit it.

**Caveats**

- The library is unofficial and reverse-engineered, not sanctioned by Garmin. A change to Garmin's login or workout format can break it without warning, so the engine must never depend on it.
- Garmin's workout format has quirks. Upload one test workout early and confirm the pace targets display correctly on your model.

Sources: [Garmin Training API](https://developer.garmin.com/gc-developer-program/training-api), [Garmin personal API coverage](https://the5krunner.com/2026/10/22/garmin-personal-api/), [python-garminconnect](https://pypi.org/project/garminconnect/0.3.4/), checked November 2026.

Start with Garmin push, then add `.fit` export and ICS as fallbacks.

**Gate to move on:** you can open the plan on your phone, finish a session, see the next week update, and see the changed sessions re-sync to your Garmin.

## Phase 5: Optional LLM layer

The app is complete without this phase. Add it only once Phases 0 to 4 work, and keep inference off the Pi.

- **Interface:** one function, `explain(context, question) -> str`, behind an adapter class.
- **Backends:** Ollama on your desktop over Tailscale (a 7B to 8B model is enough), a free API tier, or a template fallback. Switching is a config change.
- **Fallback:** if the desktop is off or a call times out after a few seconds, return templated text built from the adjustment log.
- **Boundary:** the model explains and answers questions. It never changes volume, paces or injury handling; only the rule engine does.

**Gate:** unplug the desktop and confirm every page still works.

## Risks, guardrails and first weekend

The biggest risk is a plan that ramps too fast and injures you, not a code bug. Treat the safety tests as the product.

| Risk | Mitigation |
| --- | --- |
| Unsafe progression | Hard caps in config, property-based tests, plans rejected on failure |
| SD card failure | SSD or endurance card, nightly off-device backup |
| Strava API terms or limits change | Start with manual FIT/GPX upload; Strava is optional |
| Garmin changes its unofficial login or API | Keep the exporter behind one interface; `.fit` USB copy and ICS as fallbacks |
| Scope creep | Phases are gated; skip Phase 5 until the rest works |
| Pi runs out of RAM | Single worker, no Node, no numpy; measure idle memory after each phase |

**First-weekend checklist**

- [ ] Pi booted from SSD, Tailscale connected on all three devices
- [ ] Hello-world page reachable on the phone over HTTPS
- [ ] Project repo with `pytest` and Hypothesis set up
- [ ] VDOT pace calculator with tests
- [ ] Week generator for a 10K plan, printed to the terminal
- [ ] First safety test passing

**Open questions:** the plan assumes a Garmin Forerunner 245, and supported workout targets vary by model, so test one uploaded workout early. Do you also want sessions written to Google Calendar, given that Garmin already syncs them to the watch?
