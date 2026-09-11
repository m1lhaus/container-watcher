# container-watcher

A tiny Docker Compose health watcher written in Python. It periodically checks a
comma-separated list of named containers and, if any of them are missing,
not running, or reported as `unhealthy`, it runs a full recovery cycle:

```bash
docker compose down && docker compose up -d
```

Published as a multi-arch (amd64/arm64) image at
`ghcr.io/m1lhaus/container-watcher`.

## Usage

Add it as a service in your `compose.yaml`:

```yaml
services:
  watcher:
    image: ghcr.io/m1lhaus/container-watcher:latest
    container_name: myapp-watcher
    restart: on-failure
    healthcheck:
      test: ["CMD-SHELL", "test -f /tmp/watcher.heartbeat && grep -qE '^(healthy|unhealthy)$' /tmp/watcher.heartbeat && test $(find /tmp/watcher.heartbeat -mmin -2 2>/dev/null | wc -l) -eq 1"]
      interval: 1m
      timeout: 5s
      retries: 3
      start_period: 40s
    working_dir: /workspace
    environment:
      - WATCH_SERVICES=myapp,worker,db
      - WATCH_INTERVAL=60     # how often to check the watched containers
      - RECOVERY_TIMEOUT=30   # delay before retrying a recovery after a failed condition
      - HOST_HOSTNAME=${HOSTNAME}
      - DEBUG=${WATCHER_DEBUG:-false}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ${PWD}:/workspace
```

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WATCH_SERVICES` | yes | — | Comma-separated list of container names to watch. |
| `WATCH_INTERVAL` | no | `30` | Seconds between checks. |
| `HOST_PWD` | no | current working directory | Host project directory used as the Compose working directory so the container resolves the right project files. |
| `HOST_HOSTNAME` | yes | container hostname | Host `HOSTNAME` forwarded into the Compose environment when needed by your compose file. |
| `DEBUG` | no | `false` | Set to `true` for per-check debug logging. |
| `RECOVERY_TIMEOUT` | no | `30` | Delay before the watcher retries a Compose recovery after a failed condition has been observed. |

### Requirements

- Mount the Docker socket (`/var/run/docker.sock`) so the watcher can inspect
  and manage the Compose project.
- Mount the project directory to `/workspace` so `docker compose` runs in the
  correct project directory when Compose needs to resolve the project stack.

## How it works

Every `WATCH_INTERVAL` seconds, the watcher iterates over each container name in
`WATCH_SERVICES` and inspects it via `docker inspect`.

For each watched service:

1. If the container does not exist, it is treated as failed and marks the watcher
   as unhealthy.
2. If the container exists but is not running, it is treated as failed and marks
   the watcher as unhealthy.
3. If the container has no health check configured, it logs an error and marks
   the watcher as unhealthy.
4. If the container is healthy, the watcher continues.
5. If the container is `unhealthy`, the watcher schedules a recovery.

If any watched container triggered a recovery condition, the watcher runs a full
Compose recovery once per cycle after a configurable `RECOVERY_TIMEOUT` delay:

```bash
docker compose down && docker compose up -d
```

The `RECOVERY_TIMEOUT` ensures that the watcher doesn't start a recovery when e.g. user triggered a manual compose down and up shortly before. Setting any value > 0 means that the service must be unhealthy for more than one watch interval before a recovery is attempted.

The heartbeat file (`/tmp/watcher.heartbeat`) is rewritten every cycle with the
current watcher status: `healthy` or `unhealthy`. This is intended to be used by
an outer container healthcheck, so external monitoring can see whether the
watcher is currently in a good state.

## Releasing

Push to `main` publishes the `latest` tag. Pushing a `vX.Y.Z` git tag also
publishes `X.Y.Z` and `X.Y` version tags.
