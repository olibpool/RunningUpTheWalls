"""P4-T1 spike: prove Garmin login, activity download and workout push on a real account.

Run it yourself (it prompts for your password and any MFA code):

    .venv/Scripts/python.exe scripts/garmin_spike.py

It (1) logs in and stores only the session token under ~/.config/runcoach/garmin,
(2) prints your last 5 activities and saves the newest as its original FIT zip in
.garmin-spike/ (git-ignored: it contains your GPS track), and (3) uploads one test workout
and schedules it for tomorrow. Then sync your watch and check how the workout shows.
"""

from __future__ import annotations

import getpass
import json
from datetime import date, timedelta
from pathlib import Path

from garminconnect import Garmin
from garminconnect.workout import (
    ExecutableStep,
    PaceTarget,
    RunningWorkout,
    WorkoutSegment,
    create_cooldown_step,
    create_recovery_step,
    create_repeat_group,
    create_targeted_interval_step,
    create_warmup_step,
    pace_to_mps,
)

TOKENS = Path.home() / ".config" / "runcoach" / "garmin"
OUT = Path(__file__).resolve().parents[1] / ".garmin-spike"


def login() -> Garmin:
    api = Garmin(prompt_mfa=lambda: input("MFA code: ").strip())
    try:
        api.login(str(TOKENS))  # tokens only: no password needed once they exist
    except Exception:
        print("No usable saved session; logging in with your password.")
        api = Garmin(
            input("Garmin email: ").strip(),
            getpass.getpass("Password (not stored): "),
            prompt_mfa=lambda: input("MFA code: ").strip(),
        )
        api.login(str(TOKENS))  # saves the token; the password is never written
    return api


def test_workout() -> RunningWorkout:
    # PaceTarget limits are m/s and the slower pace is the lower limit.
    target = PaceTarget(lower_limit=pace_to_mps(5, 10, "km"), upper_limit=pace_to_mps(4, 55, "km"))
    work = create_targeted_interval_step(180, 3, target)
    rest = create_recovery_step(120, 4)
    # Garmin's own exports number steps across the whole workout and tag a repeat's children.
    work.childStepId = rest.childStepId = 1
    steps: list[ExecutableStep] = [
        create_warmup_step(600, 1),
        create_repeat_group(3, [work, rest], 2),  # type: ignore[list-item]
        create_cooldown_step(600, 5),
    ]
    return RunningWorkout(
        workoutName="RC spike W1 Intervals",
        estimatedDurationInSecs=600 + 3 * 300 + 600,
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType={"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
                workoutSteps=steps,  # type: ignore[arg-type]
            )
        ],
    )


def main() -> None:
    api = login()
    print("Logged in.\n")

    page = api.get_activities(0, 5)
    activities = page if isinstance(page, list) else []
    for a in activities:
        kind = (a.get("activityType") or {}).get("typeKey")
        print(
            a.get("activityId"),
            a.get("startTimeGMT"),
            f"type={kind!r}",
            f"{a.get('duration', 0) / 60:.1f} min",
            f"{a.get('distance', 0) / 1000:.2f} km",
        )
    if activities:
        OUT.mkdir(exist_ok=True)
        zip_path = OUT / f"{activities[0]['activityId']}.zip"
        zip_path.write_bytes(
            api.download_activity(
                str(activities[0]["activityId"]), api.ActivityDownloadFormat.ORIGINAL
            )
        )
        print(f"\nSaved {zip_path} (the original FIT inside a zip). Do not commit it.")

    uploaded = api.upload_running_workout(test_workout())
    workout_id = uploaded["workoutId"]
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    scheduled = api.schedule_workout(workout_id, tomorrow)
    print(f"\nUploaded workout {workout_id} and scheduled it for {tomorrow}.")
    print(json.dumps(scheduled, indent=2)[:600])
    print("\nNow sync your watch and report how the steps, repeats and paces look.")


if __name__ == "__main__":
    main()
