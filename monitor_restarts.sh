#!/bin/bash
set -eu
STATE_FILE=/var/lib/bike-bot/previous-restarts
ALERT_THRESHOLD=${ALERT_THRESHOLD:-3}
mkdir -p "$(dirname "$STATE_FILE")"
current=$(systemctl show bike-bot -p NRestarts --value)
previous=0
[ -f "$STATE_FILE" ] && previous=$(cat "$STATE_FILE")
printf '%s\n' "$current" > "$STATE_FILE"
if [ "$current" -ge $((previous + ALERT_THRESHOLD)) ]; then
  token=$(sed -n 's/^BOT_TOKEN=//p' /opt/bike_bot/.env)
  admins=$(sed -n 's/^ADMIN_IDS=//p' /opt/bike_bot/.env)
  IFS=',' read -ra ids <<< "$admins"
  for id in "${ids[@]}"; do
    curl -fsS -X POST "https://api.telegram.org/bot${token}/sendMessage" \
      --data-urlencode "chat_id=${id}" \
      --data-urlencode "text=bike-bot restart alert: NRestarts=${current}" >/dev/null || true
  done
fi
