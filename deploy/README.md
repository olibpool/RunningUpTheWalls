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
   sudo -u runcoach mkdir -p /home/runcoach/runcoach-inbox
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
   sudo systemctl enable --now runcoach.service sync.timer backup.timer
   sudo tailscale serve --bg 8000     # publishes http://127.0.0.1:8000 over HTTPS on your tailnet
   ```
7. **Phone.** Open the HTTPS tailnet address, then "Add to Home Screen".

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
