#!/usr/bin/env bash
# Conservative VPS hygiene — run by cron (daily). Safe by construction:
#   • prunes only resources Docker itself considers unused (stopped containers,
#     DANGLING images, build cache, unused networks) — running services untouched.
#   • NEVER `docker image prune -a`  (would delete our tagged opt-app-* images that
#     the build-on-Mac → save/load flow relies on).
#   • NEVER `docker volume prune`    (would risk postgres_data / the paper DB).
#   • truncates only very large container json logs (we read the DB, not docker logs).
#   • alerts (Telegram) when root disk is under pressure; never deletes app data.
# Logs every action to $LOG. Reuses the bot's existing TG creds from $APP/.env.
set -uo pipefail
APP=/root/opt-app
LOG=$APP/vps_cleanup.log
DISK_ALERT_PCT=80          # Telegram if root usage >= this
LOG_TRUNC_MB=200           # truncate a container json log only above this size
TMP_ARTIFACT_DAYS=3        # delete /tmp/*.tar.gz deploy artifacts older than this
set -a; . "$APP/.env" 2>/dev/null || true; set +a

ts() { date -u '+%Y-%m-%d %H:%M:%S UTC'; }
say() { echo "$(ts) $*" >>"$LOG"; }

say "=== cleanup start ==="
BEFORE=$(df -h / | awk 'NR==2{print $3" used / "$5}')

# 1) Docker safe prunes (each skips in-use resources by Docker's own semantics).
say "container prune: $(docker container prune -f 2>&1 | tail -1)"
say "image prune (dangling): $(docker image prune -f 2>&1 | tail -1)"
say "builder prune (>168h): $(docker builder prune -f --filter until=168h 2>&1 | tail -1)"
say "network prune: $(docker network prune -f 2>&1 | tail -1)"

# 2) Truncate oversized container json logs (safe: docker keeps appending).
while IFS= read -r f; do
  [ -f "$f" ] || continue
  sz=$(du -m "$f" 2>/dev/null | cut -f1)
  if [ "${sz:-0}" -ge "$LOG_TRUNC_MB" ]; then
    : > "$f" && say "truncated big log ($sz MB): $f"
  fi
done < <(find /var/lib/docker/containers -name '*-json.log' 2>/dev/null)

# 3) Old deploy artifacts / our own log files.
find /tmp -maxdepth 1 -name '*.tar.gz' -mtime +"$TMP_ARTIFACT_DAYS" -print -delete 2>/dev/null \
  | while read -r f; do say "removed old artifact: $f"; done
[ -f /root/deploy_paper.log ] && : > /root/deploy_paper.log

AFTER=$(df -h / | awk 'NR==2{print $3" used / "$5}')
USE=$(df / | awk 'NR==2{gsub(/%/,"",$5); print $5}')
say "disk before: $BEFORE | after: $AFTER"
say "=== cleanup done (root ${USE}% used) ==="

# 4) Disk-pressure alert (alert only — never auto-delete app data).
if [ "${USE:-0}" -ge "$DISK_ALERT_PCT" ] && [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  MSG="🧹 VPS диск под давлением: root ${USE}%% занято (порог ${DISK_ALERT_PCT}%%).%0AПроверь \`docker system df\` и логи. Volume/образы НЕ чистятся авто — нужно вручную."
  curl -s --max-time 15 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" --data "text=${MSG}" >/dev/null || true
fi
