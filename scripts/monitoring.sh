#!/bin/sh
# Local monitoring for the AI agent: Prometheus (:9090) + Grafana (:3000), configured from monitoring/.
#
#   scripts/monitoring.sh start | stop | status
#   scripts/monitoring.sh export     # write dashboard edits made in the UI back to monitoring/grafana/dashboards
#
# Grafana: http://localhost:3000 -> dashboard "AI agent: LLM calls, tokens, latency".
# Viewing needs no login; to edit, sign in as admin / admin (local only).
# Data comes from C1's /metrics (python -m ai_analyzer summarizer, port 9101) and the ai_analyzer
# Postgres database. Installed with: brew install prometheus grafana
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MONITORING_DIR="$ROOT/monitoring"
DATA="$MONITORING_DIR/data"
LOGS="$ROOT/logs"
GRAFANA_HOME="$(brew --prefix grafana)/share/grafana"

start() {
  mkdir -p "$DATA/prometheus" "$DATA/grafana" "$LOGS"

  if ! is_running prometheus; then
    prometheus \
      --config.file="$MONITORING_DIR/prometheus.yml" \
      --storage.tsdb.path="$DATA/prometheus" \
      --web.listen-address=127.0.0.1:9090 \
      >> "$LOGS/prometheus.log" 2>&1 &
    echo $! > "$DATA/prometheus.pid"
  fi

  if ! is_running grafana; then
    # Local only: listens on 127.0.0.1. Anyone can view; sign in as admin/admin to edit.
    MONITORING_DIR="$MONITORING_DIR" \
    PG_USER="${PG_USER:-$(whoami)}" PG_PASSWORD="${PG_PASSWORD:-}" PG_HOST=localhost:5432 \
    PROMETHEUS_URL=http://localhost:9090 \
    GF_PATHS_PROVISIONING="$MONITORING_DIR/grafana/provisioning" \
    GF_PATHS_DATA="$DATA/grafana" \
    GF_PATHS_LOGS="$LOGS" \
    GF_SERVER_HTTP_ADDR=127.0.0.1 \
    GF_SERVER_HTTP_PORT=3000 \
    GF_AUTH_ANONYMOUS_ENABLED=true \
    GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer \
    GF_SECURITY_ADMIN_USER=admin \
    GF_SECURITY_ADMIN_PASSWORD=admin \
    GF_ANALYTICS_REPORTING_ENABLED=false \
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH="$MONITORING_DIR/grafana/dashboards/ai_agent.json" \
    grafana server --homepath "$GRAFANA_HOME" >> "$LOGS/grafana.log" 2>&1 &
    echo $! > "$DATA/grafana.pid"
  fi
  status
}

stop() {
  for name in prometheus grafana; do
    if is_running "$name"; then
      kill "$(cat "$DATA/$name.pid")" && echo "stopped $name"
    fi
    rm -f "$DATA/$name.pid"
  done
}

status() {
  for name in prometheus grafana; do
    if is_running "$name"; then echo "$name: running (pid $(cat "$DATA/$name.pid"))"; else echo "$name: stopped"; fi
  done
  echo "Prometheus http://localhost:9090   Grafana http://localhost:3000"
}

export_dashboards() {
  # The dashboard as Grafana has it now (including UI edits) -> its JSON file in the repo.
  curl -sf -u admin:admin "http://localhost:3000/api/dashboards/uid/ai-agent" | python3 -c '
import json, sys
dashboard = json.load(sys.stdin)["dashboard"]
for key in ("id", "version"):
    dashboard.pop(key, None)
print(json.dumps(dashboard, indent=2))' > "$MONITORING_DIR/grafana/dashboards/ai_agent.json"
  echo "exported to monitoring/grafana/dashboards/ai_agent.json"
}

is_running() {
  [ -f "$DATA/$1.pid" ] && kill -0 "$(cat "$DATA/$1.pid")" 2>/dev/null
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  export) export_dashboards ;;
  *) echo "usage: $0 start | stop | status | export"; exit 1 ;;
esac
