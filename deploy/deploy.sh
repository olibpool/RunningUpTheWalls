#!/usr/bin/env bash
# Coachal auto-deploy, run by deploy.timer as root: if GitHub has a newer v* tag, back up the
# database, switch to the tag, restart, and roll back (code and database) if /healthz fails.
set -euo pipefail

: "${APP_DIR:=/opt/runcoach}"
: "${RUN_AS:=runcoach}"
: "${UV:=/home/runcoach/.local/bin/uv}"
: "${SERVICE:=runcoach.service}"
: "${HEALTH_URL:=http://127.0.0.1:8000/healthz}"
: "${RUNCOACH_STORAGE__DB_PATH:?set in /etc/runcoach.env}"

as_app() { runuser -u "$RUN_AS" -- "$@"; }
git_app() { as_app git -C "$APP_DIR" "$@"; }
# A tag that failed is skipped until a newer one is pushed, so it isn't retried every 10 minutes.
failed_file="$(dirname "$RUNCOACH_STORAGE__DB_PATH")/deploy-failed"

healthy() {
  for _ in $(seq 30); do
    curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

switch_to() {
  git_app checkout --quiet --detach "$1"
  (cd "$APP_DIR" && as_app "$UV" sync --frozen --quiet)
}

git_app fetch --quiet --tags --force origin
latest=$(git_app tag --list 'v*' --sort=-v:refname | head -n 1)
current=$(git_app rev-parse HEAD)
if [ -z "$latest" ] || [ "$(git_app rev-parse "$latest^{commit}")" = "$current" ]; then
  exit 0
fi
if [ "$latest" = "$(cat "$failed_file" 2>/dev/null)" ]; then
  exit 0
fi
echo "Deploying $latest (from $current)"

# Stop first so nothing writes between the backup and the new code's migrations.
systemctl stop "$SERVICE"
backup=$(cd "$APP_DIR" && as_app .venv/bin/python -m runcoach backup | sed -n 's/^Backed up to //p')
[ -n "$backup" ] || { echo "Backup failed; not deploying"; systemctl start "$SERVICE"; exit 1; }

if switch_to "$latest" && systemctl start "$SERVICE" && healthy; then
  echo "Deployed $latest"
  exit 0
fi

echo "$latest" > "$failed_file"
echo "$latest failed its health check; rolling back to $current and restoring $backup"
systemctl stop "$SERVICE"
switch_to "$current"
db="$RUNCOACH_STORAGE__DB_PATH"
as_app cp "$backup" "$db"
rm -f "$db-wal" "$db-shm"  # stale WAL pages from the failed version would replay over the restore
systemctl start "$SERVICE"
healthy && echo "Rolled back to $current" || echo "Rollback is unhealthy too: check journalctl -u $SERVICE"
exit 1
