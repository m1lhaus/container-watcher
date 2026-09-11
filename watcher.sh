#!/bin/sh
# Compose-aware health watcher.
# For each watched service, if its container is unhealthy, waits for its
# tailscale sidecar (named "<WATCH_TS_PREFIX><service>") to be healthy, then
# runs:
#   docker rm -f <service> && docker compose up -d <service>
# Removing first and using --no-deps-free "up -d" (no --force-recreate)
# avoids Compose touching the sidecar (already running via restart:always),
# preventing a name-conflict error.
# Set WATCH_TS_PREFIX="" to disable the sidecar check entirely.

SERVICES="${WATCH_SERVICES:?WATCH_SERVICES must be set}"
INTERVAL="${WATCH_INTERVAL:-30}"
TS_PREFIX="${WATCH_TS_PREFIX-ts-}"

dbg() {
  [ "${DEBUG}" = "true" ] && echo "$(date -Iseconds) [DEBUG] $*"
}

# Map a watched service to its tailscale sidecar container name
ts_sidecar() {
  [ -n "$TS_PREFIX" ] && echo "${TS_PREFIX}$1"
}

echo "$(date -Iseconds) Compose watcher started (interval=${INTERVAL}s, services: ${SERVICES})"

while true; do
  sleep "$INTERVAL"
  touch /tmp/watcher.heartbeat
  for svc in $SERVICES; do
    status=$(docker inspect --format='{{.State.Health.Status}}' "$svc" 2>/dev/null | tail -1)
    [ -z "$status" ] && status="missing"
    dbg "${svc} health=${status}"
    if [ "$status" = "unhealthy" ] || [ "$status" = "missing" ]; then
      ts=$(ts_sidecar "$svc")
      if [ -n "$ts" ]; then
        ts_running=$(docker inspect --format='{{.State.Running}}' "$ts" 2>/dev/null | tail -1)
        [ -z "$ts_running" ] && ts_running="false"
        ts_status=$(docker inspect --format='{{.State.Health.Status}}' "$ts" 2>/dev/null | tail -1)
        [ -z "$ts_status" ] && ts_status="missing"
        dbg "${ts} running=${ts_running} health=${ts_status}"
        if [ "$ts_running" != "true" ] || [ "$ts_status" != "healthy" ]; then
          echo "$(date -Iseconds) ${svc} is unhealthy but ${ts} is running=${ts_running}/health=${ts_status} — waiting for sidecar"
          continue
        fi
      fi
      echo "$(date -Iseconds) ${svc} is ${status} — removing and recreating via docker compose"
      docker rm -f "$svc"
      PWD="$HOST_PWD" HOSTNAME="$HOST_HOSTNAME" docker compose up -d "$svc"
      echo "$(date -Iseconds) ${svc} recreated"
    fi
  done
done
