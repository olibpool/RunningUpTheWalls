# Deploying Coachal on a Raspberry Pi

These steps are P0-T7 in the implementation plan; they need you at the Pi. Run commands as a user
with `sudo`. Check `tailscale serve --help` before step 6, because its syntax has changed between
versions.

1. **Flash and boot.** Raspberry Pi OS Lite (64-bit), SSH enabled, booting from an SSD or an
   endurance SD card.
2. **Tailscale.** Install it on the Pi, your desktop and your phone, sign in on all three, and
   check they can reach each other.
3. **App user and folders.**
   ```bash
   sudo useradd --system --create-home --shell /usr/sbin/nologin runcoach
   sudo mkdir -p /opt/runcoach /var/lib/runcoach /var/backups/runcoach
   sudo chown runcoach:runcoach /opt/runcoach /var/lib/runcoach /var/backups/runcoach
   sudo -u runcoach mkdir -p /home/runcoach/runcoach-inbox /home/runcoach/.config/runcoach
   ```
4. **Code and Python.** Install `uv` for the `runcoach` user, clone the repo into `/opt/runcoach`,
   then `cd /opt/runcoach && uv sync --frozen`.
5. **Settings.** `sudo cp deploy/runcoach.env.example /etc/runcoach.env`, edit it (database path,
   iCal address, weather location), then `sudo chown root:runcoach /etc/runcoach.env && sudo chmod
640 /etc/runcoach.env`. Availability windows and other defaults can go in
   `/opt/runcoach/config/local.toml`.
6. **Services.**
   ```bash
   sudo cp deploy/*.service deploy/*.timer /etc/systemd/system/
   sudo systemd-analyze verify /etc/systemd/system/runcoach.service \
       /etc/systemd/system/sync.service /etc/systemd/system/backup.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now runcoach.service backup.timer   # the app syncs by itself ([sync] auto_minutes)
   sudo tailscale serve --bg 8000     # publishes http://127.0.0.1:8000 over HTTPS on your tailnet
   ```
7. **Phone.** Open the HTTPS tailnet address, then "Add to Home Screen".
8. **Garmin (optional).** In the app, open Settings, Garmin Connect and log in (with the one-time
   code if Garmin asks). Only a session token is saved, under
   `/home/runcoach/.config/runcoach/garmin`, and the Garmin pull switches on. To push workouts,
   set *Watch workouts* to Garmin Connect. If the token expires, the header chip shows a sync
   issue; connect again in Settings. (`runcoach garmin login` on the command line still works.)

   `sync.timer` is only needed with `RUNCOACH_SYNC__AUTO_MINUTES=0`. With both on, the app skips
   its own sync while the timer's last one is recent.

9. **Auto-deploy (optional).** The Pi checks GitHub every 10 minutes and deploys the newest `v*`
   tag by itself:
   ```bash
   sudo cp deploy/deploy.service deploy/deploy.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now deploy.timer
   ```
   If the repo is private, give the Pi a read-only deploy key first: as `runcoach`, run
   `ssh-keygen -t ed25519`, add the `.pub` under the repo's *Settings → Deploy keys* (read-only),
   and point the clone at `git@github.com:...` with `git remote set-url origin`.

## Releasing

1. Open a pull request into `main`. CI (`.github/workflows/ci.yml`) runs ruff, ruff format, mypy and
   pytest on Python 3.12 and 3.14. To block merging on failure, add a branch rule for `main`
   requiring the `checks` jobs (*Settings → Rules → Rulesets*).
2. After merging, tag a release on `main`: `git tag v1.2.0 && git push origin v1.2.0`.
3. Within about 10 minutes the Pi stops the app, backs up the database, switches to the tag, runs
   `uv sync --frozen`, starts the app and checks `/healthz`. If the health check fails it switches
   back to the previous version, restores the backup and skips that tag until a newer one appears.
   Watch it with `journalctl -u deploy.service`; deploy now with `sudo systemctl start deploy.service`.

Deploys only go forwards. To go back, tag the old commit with a higher version number
(`git tag v1.2.1 <old-commit>`), or follow the restore steps below for the data.

**Gate (Phase 0):** reboot the Pi; with no manual steps the page loads on the phone over HTTPS,
`/healthz` returns `{"status": "ok", ...}`, and `sudo systemctl start backup.service` leaves a file
in `/var/backups/runcoach/`.

**Restore from a backup:** stop the app (`sudo systemctl stop runcoach.service sync.timer`), copy
the chosen `/var/backups/runcoach/runcoach-*.db` over `/var/lib/runcoach/runcoach.db` (as the
`runcoach` user), and start the services again.

**Memory check (working rule 9):** `ps -o rss= -p $(pgrep -f "uvicorn runcoach")`, recorded in
`docs/perf.md`.

**Things only you can verify:** that a cross-site POST check still accepts forms when going through
`tailscale serve` (it compares `Origin` with `X-Forwarded-Host` or `Host`), and that the
installed home-screen app opens the cached week page with the network off.
