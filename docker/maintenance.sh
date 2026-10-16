#!/bin/sh
# The scheduled jobs: sync-all every SYNC_ALL_SECONDS (default 20 min), renew-watches once a day.
every=${SYNC_ALL_SECONDS:-1200}
last_renew=0
while true; do
  now=$(date +%s)
  if [ $((now - last_renew)) -ge 86400 ]; then
    python -m app.cli renew-watches && last_renew=$now
  fi
  python -m app.cli sync-all
  sleep "$every"
done
