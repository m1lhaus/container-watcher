# container-watcher

A tiny Docker Compose health watcher written in Python. It periodically checks a
comma-separated list of named containers and, if any of them are missing,
not running, or reported as `unhealthy`, it runs a full recovery cycle:

```bash
docker stop <watched-container>
docker rm -f <watched-container>
docker compose up -d
```

The stop and remove steps are performed for every container in
`WATCH_SERVICES`. The watcher service is left running so it can recover the
other services from inside the Compose project.

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
| `WATCH_SERVICES` | yes | — | Comma-separated list of container names to watch; do not include the watcher container. |
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
- Set `WATCH_SERVICES` to the actual container names that Compose creates, and
  do not include the watcher container itself.

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

If any watched container triggered a recovery condition, the watcher runs a
Compose recovery once per cycle after a configurable `RECOVERY_TIMEOUT` delay.
It stops and removes each container in `WATCH_SERVICES`, then asks Compose to
recreate the missing containers:

```bash
docker stop <watched-container>
docker rm -f <watched-container>
docker compose up -d
```

The `RECOVERY_TIMEOUT` ensures that the watcher doesn't start a recovery when,
for example, a user triggered a manual Compose operation shortly before. Setting
any value > 0 means that the service must be unhealthy for more than one watch
interval before a recovery is attempted.

The heartbeat file (`/tmp/watcher.heartbeat`) is rewritten every cycle with the
current watcher status: `healthy` or `unhealthy`. This is intended to be used by
an outer container healthcheck, so external monitoring can see whether the
watcher is currently in a good state.

## Example use case

When self-hosting services on your Tailnet, you may run your applications in an
app-and-Tailscale-sidecar setup. In this arrangement, the application service
shares a network with a Tailscale sidecar service defined in the same Compose
file. See simplified example below.

```yaml
services:
  ts-otter-wiki:
    image: tailscale/tailscale:latest
    container_name: ts-otter-wiki
    ...
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://127.0.0.1:41234/healthz"]
      interval: 1m
      timeout: 10s
      retries: 3
      start_period: 10s
    restart: unless-stopped
  
  otter-wiki:
    image: redimp/otterwiki:2
    container_name: otter-wiki
    restart: unless-stopped
    network_mode: "service:ts-otter-wiki"
    depends_on:
      ts-otter-wiki:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -fs http://127.0.0.1:80/ > /dev/null && curl -fs http://127.0.0.1:41234/healthz > /dev/null"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    ...

  watcher:
    image: ghcr.io/m1lhaus/container-watcher:latest
    container_name: otter-wiki-watcher
    restart: on-failure
    healthcheck:
      test: ["CMD-SHELL", "test -f /tmp/watcher.heartbeat && grep -qE '^(healthy|unhealthy)$' /tmp/watcher.heartbeat && test $(find /tmp/watcher.heartbeat -mmin -2 2>/dev/null | wc -l) -eq 1"]
      interval: 1m
      timeout: 5s
      retries: 3
      start_period: 40s
    ...
```

The application service depends on the Tailscale sidecar for network
connectivity. If the sidecar crashes, Docker automatically restarts it according
to its restart policy. However, the restarted sidecar receives a new network
namespace, while the application service remains attached to the old namespace
and loses its network connectivity. Both services may appear healthy even though
the setup is no longer working.

You can use Docker's built-in health checks to monitor connectivity between the
application and the Tailscale sidecar. The sidecar exposes a health-check
endpoint that the application can query. If the sidecar is restarted, the
application can no longer reach the endpoint, so its health check fails and the
application is marked as unhealthy. The watcher detects this state, stops and
removes the affected containers, and asks Compose to recreate them, restoring
network connectivity.