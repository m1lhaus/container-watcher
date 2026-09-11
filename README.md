# container-watcher

A tiny, generic Docker Compose "health watcher" sidecar. It periodically
checks the health status of one or more containers in your Compose project
and, if a container is unhealthy (or missing), removes and recreates it via
`docker compose up -d`.

Originally written for setups where a service depends on a Tailscale sidecar
(`network_mode: service:ts-<name>`) — if the sidecar isn't healthy yet, the
watcher waits instead of recreating the dependent service prematurely.

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
      test: ["CMD-SHELL", "find /tmp/watcher.heartbeat -mmin -2 2>/dev/null | grep -q ."]
      interval: 1m
      timeout: 5s
      retries: 3
      start_period: 40s
    working_dir: /workspace
    environment:
      - WATCH_SERVICES=myapp            # space-separated list of service/container names to watch
      - WATCH_INTERVAL=30                # seconds between checks
      - WATCH_TS_PREFIX=ts-              # sidecar container prefix; set to "" to disable the sidecar check
      - COMPOSE_PROJECT_NAME=myapp
      - HOST_PWD=${PWD}                  # needed so docker compose inside the container resolves bind mount paths correctly
      - HOST_HOSTNAME=${HOSTNAME}        # needed so ${HOSTNAME} in compose.yaml resolves to the host name, not the container ID
      - DEBUG=${WATCHER_DEBUG:-false}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ${PWD}:/workspace
```

### Environment variables

| Variable            | Required | Default | Description |
|---------------------|----------|---------|-------------|
| `WATCH_SERVICES`     | yes      | —       | Space-separated list of Compose service/container names to watch. |
| `WATCH_INTERVAL`     | no       | `30`    | Seconds to sleep between health checks. |
| `WATCH_TS_PREFIX`    | no       | `ts-`   | Prefix used to derive each service's Tailscale sidecar container name (`<prefix><service>`). Set to an empty string to skip the sidecar check entirely. |
| `HOST_PWD`           | no       | —       | Set to the host `${PWD}` so `docker compose` (run from inside the watcher container) resolves relative bind-mount paths against the real project directory. |
| `HOST_HOSTNAME`      | no       | —       | Set to the host `${HOSTNAME}` so any `${HOSTNAME}` interpolation in your compose file resolves correctly instead of resolving to the watcher container's own hostname. |
| `DEBUG`              | no       | `false` | Set to `true` for verbose per-check logging. |

### Requirements

- Mount the Docker socket (`/var/run/docker.sock`) so the watcher can inspect
  and recreate containers.
- Mount the project directory to `/workspace` (`working_dir: /workspace`) so
  `docker compose up -d <service>` runs against the right project/compose
  file.

## How it works

Every `WATCH_INTERVAL` seconds, for each name in `WATCH_SERVICES`:

1. Inspect the container's health status.
2. If `unhealthy` or missing, and a sidecar prefix is configured, check that
   `<prefix><service>` is running and healthy — if not, skip this cycle and
   wait.
3. Otherwise, `docker rm -f <service>` and `docker compose up -d <service>`
   to recreate it.

A heartbeat file (`/tmp/watcher.heartbeat`) is touched every cycle for use in
the container's own healthcheck.

## Releasing

Push to `main` publishes the `latest` tag. Pushing a `vX.Y.Z` git tag also
publishes `X.Y.Z` and `X.Y` version tags.
