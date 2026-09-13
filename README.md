# container-watcher

A tiny Docker Compose health watcher written in Python. It periodically checks a
comma-separated list of Compose service names and, if any of them are missing,
not running, or reported as `unhealthy`, it runs a full recovery cycle:

```bash
docker stop <container-id>
docker rm -f <container-id>
docker compose up -d <watched-service>
```

The watcher finds each service's container using the Compose project and service
labels, so watched services do not need a manually configured `container_name`.
The stop and remove steps are performed for every service in `WATCH_SERVICES`.

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
      test: ["CMD-SHELL", "test -f /tmp/watcher.heartbeat && grep -q '^healthy$' /tmp/watcher.heartbeat && test $(find /tmp/watcher.heartbeat -mmin -2 2>/dev/null | wc -l) -eq 1"]
      interval: 1m
      timeout: 5s
      retries: 3
      start_period: 40s
    working_dir: /workspace
    environment:
      - WATCH_SERVICES=myapp,worker,db  # Compose service names
      - WATCH_INTERVAL=60               # how often to check the watched services
      - RECOVERY_TIMEOUT=30   # delay before retrying a recovery after a failed condition
      - COMPOSE_PROJECT_NAME=myapp  # Compose project name (folder name)
      - HOST_PWD=${PWD}
      - HOST_HOSTNAME=${HOSTNAME}
      - DEBUG=${WATCHER_DEBUG:-false}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ${PWD}:/workspace
```

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WATCH_SERVICES` | yes | — | Comma-separated list of Compose service names to watch; do not include the watcher service. |
| `WATCH_INTERVAL` | no | `30` | Seconds between checks. |
| `COMPOSE_PROJECT_NAME` | yes | — | Compose project name used to identify the correct project and its service containers. Usually the folder name of the project. |
| `HOST_PWD` | yes | — | Absolute path to the host project directory. It is used as the Compose project directory and should be mounted at `/workspace`. |
| `HOST_HOSTNAME` | yes | — | Host `HOSTNAME` forwarded into the Compose environment when needed by your compose file. |
| `DEBUG` | no | `false` | Set to `true` for per-check debug logging. |
| `RECOVERY_TIMEOUT` | no | `30` | Delay before the watcher retries a Compose recovery after a failed condition has been observed. |

### Requirements

- Mount the Docker socket (`/var/run/docker.sock`) so the watcher can inspect
  and manage the Compose project.
- Mount the project directory to `/workspace` so `docker compose` runs in the
  correct project directory when Compose needs to resolve the project stack.
  The mount should match `HOST_PWD`.
- Provide the Compose project files at `/workspace/compose.yaml` and
  `/workspace/.env`.
- Set `WATCH_SERVICES` to service names defined under `services:` in your Compose
  file. Do not include the watcher service itself.

## How it works

Every `WATCH_INTERVAL` seconds, the watcher iterates over each Compose service
name in `WATCH_SERVICES`, finds its container using the Compose project and
service labels, and inspects it via `docker inspect`.

For each watched service:

1. If the container does not exist (or cannot be inspected), the watcher marks
   itself as unhealthy and schedules a recovery.
2. If the container exists but is not running, the watcher schedules a
   recovery.
3. If the container has no health check configured, the watcher logs an error
   and marks itself as unhealthy.
4. If the container is healthy, the watcher continues.
5. If the container is `unhealthy`, the watcher schedules a recovery.

In other words, the watcher reports itself as unhealthy only when a watched
container is missing, a watched service has no health check configured, or a
recovery attempt has failed. During the `RECOVERY_TIMEOUT` grace period it
keeps reporting `healthy`, so a brief manual Compose operation does not flap
its status.

If any watched service triggered a recovery condition, the watcher runs a
Compose recovery once per cycle after a configurable `RECOVERY_TIMEOUT` delay.
It stops and removes the container for each service in `WATCH_SERVICES`, then
asks Compose to recreate exactly those services. Any other services defined in
the Compose file (including the watcher itself) are left untouched. The watcher
runs a command equivalent to:

```bash
docker stop <container-id>
docker rm -f <container-id>
docker compose \
  --project-directory "$HOST_PWD" \
  -f /workspace/compose.yaml \
  --env-file /workspace/.env \
  up -d <watched-service> [...]
```

The `RECOVERY_TIMEOUT` ensures that the watcher doesn't start a recovery when,
for example, a user triggered a manual Compose operation shortly before. Setting
any value > 0 means that the service must be unhealthy for more than one watch
interval before a recovery is attempted.

The heartbeat file (`/tmp/watcher.heartbeat`) is rewritten every cycle with the
current watcher status: `healthy` or `unhealthy`. The example healthchecks above
treat the watcher as healthy only when the file exists, is fresh, and contains
`healthy`; a missing, stale, or `unhealthy` heartbeat fails the check, so Docker
and external monitoring can see when the watcher (or one of its watched
services) is not in a good state. Keep `WATCH_INTERVAL` comfortably below
the staleness window of that healthcheck (2 minutes in the examples above);
otherwise the heartbeat file ages out between checks and the watcher container
itself is reported as unhealthy.

## Example use case

When self-hosting services on your Tailnet, you may run your applications in an
app-and-Tailscale-sidecar setup. In this arrangement, the application service
shares a network with a Tailscale sidecar service defined in the same Compose
file. See simplified example below.

```yaml
services:
  ts-otter-wiki:
    image: tailscale/tailscale:latest
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
    restart: on-failure
    working_dir: /workspace
    environment:
      - WATCH_SERVICES=ts-otter-wiki,otter-wiki
      - COMPOSE_PROJECT_NAME=otter-wiki-on-tailscale  # name of the folder
      - HOST_PWD=${PWD} 
      - HOST_HOSTNAME=${HOSTNAME} 
    healthcheck:
      test: ["CMD-SHELL", "test -f /tmp/watcher.heartbeat && grep -q '^healthy$' /tmp/watcher.heartbeat && test $(find /tmp/watcher.heartbeat -mmin -2 2>/dev/null | wc -l) -eq 1"]
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