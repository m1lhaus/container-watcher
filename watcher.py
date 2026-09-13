#!/usr/bin/env python3
"""Container health watcher for Docker Compose projects.

This script periodically inspects a comma-separated list of watched Compose services
from WATCH_SERVICES. For each service it checks whether the container is running, 
and whether it exposes a Docker health status.

If a watched service is missing, not running, or reports "unhealthy", the
watcher marks itself unhealthy and waits for a recovery timeout before running a
recovery that stops and removes the watched containers before asking Compose to
recreate them:

    docker stop <container-id>
    docker rm -f <container-id>
    docker compose up -d <service>

The heartbeat file stored at /tmp/watcher.heartbeat is rewritten every cycle with
"healthy" or "unhealthy" so parent container health checks can tell whether the
watcher is currently in a good state.

Required environment variables:
    WATCH_SERVICES - comma-separated list of Compose service names to watch
    COMPOSE_PROJECT_NAME - Compose project containing the watched services

Optional environment variables:
    WATCH_INTERVAL - seconds between checks (default: 30)
    DEBUG          - set to "true" for verbose logs (default: "false")
    HOST_PWD       - host project directory used as Compose working dir
    HOST_HOSTNAME  - host hostname forwarded into docker compose environment
    RECOVERY_TIMEOUT - seconds to wait before retrying a forced recovery
"""

import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HEARTBEAT = Path("/tmp/watcher.heartbeat")
DOCKER_INSPECT_FORMAT = (
    "{{.State.Running}}|"
    "{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}"
)


def log(message, error=False):
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"{timestamp} {message}", file=sys.stderr if error else sys.stdout, flush=True)


def debug(message):
    if os.environ.get("DEBUG", "false") == "true":
        log(f"[DEBUG] {message}")


def write_heartbeat(status):
    # write to a temp file and rename so the healthcheck never observes a half-written file
    tmp_path = HEARTBEAT.with_suffix(".tmp")
    tmp_path.write_text(f"{status}\n", encoding="ascii")
    tmp_path.replace(HEARTBEAT)


def container_id_for_service(service, project, environment):
    result = subprocess.run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            f"label=com.docker.compose.service={service}",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        log(
            f"[ERROR] docker ps failed for service {service}: {result.stderr.strip()}",
            error=True,
        )
        return None

    container_ids = result.stdout.splitlines()
    if len(container_ids) > 1:
        log(
            f"[ERROR] multiple containers found for service {service} "
            f"in project {project}: {container_ids}",
            error=True,
        )
        return None
    return container_ids[0] if container_ids else None


def inspect_container(container_id, environment):
    result = subprocess.run(
        ["docker", "inspect", f"--format={DOCKER_INSPECT_FORMAT}", container_id],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        if result.returncode != 0:
            log(
                f"[ERROR] docker inspect failed for container {container_id[:12]}: {result.stderr.strip()}",
                error=True,
            )
        return None

    running, health = result.stdout.strip().split("|", 1)
    return running == "true", health


def compose_environment():
    environment = os.environ.copy()
    environment["HOSTNAME"] = os.environ.get("HOST_HOSTNAME", environment.get("HOSTNAME", ""))
    environment["PWD"] = os.environ.get("HOST_PWD", os.getcwd())
    return environment


def recover(services):
    log("Running Compose recovery")
    environment = compose_environment()
    project = environment["COMPOSE_PROJECT_NAME"]

    for service in services:
        container_id = container_id_for_service(service, project, environment)
        if container_id is None:
            log(f"{service} container is already missing")
            continue
        log(f"Stopping and removing service {service} ({container_id[:12]})")
        subprocess.run(["docker", "stop", container_id], env=environment, check=False)
        subprocess.run(["docker", "rm", "-f", container_id], env=environment, check=False)

    # adjust cwd so the script can be executed separately (e.g. debugger)
    running_in_docker = "/workspace" == os.getcwd()
    proc_wd = os.getcwd() if running_in_docker else environment["PWD"]

    debug(f"Compose services to recover: {services}")
    up = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",              # so that relative paths in compose files are resolved correctly (e.g. binding volumes)
            environment["PWD"],
            "-f",
            proc_wd + "/compose.yaml",
            "--env-file",
            proc_wd + "/.env",                  # .env file is not picked up automatically when using --project-directory
            "up",
            "-d",
            *dict.fromkeys(services),           # otherwise compose kills all services from the compose file including the watcher
        ],
        cwd=proc_wd,
        env=environment,
        check=False,
    )

    if up.returncode != 0:
        log("[ERROR] docker compose up -d failed", error=True)
        return False

    log("Compose recovery completed")
    return True


def stop_watcher(signum, _frame):
    log(f"Received signal {signum}; stopping")
    raise SystemExit(0)


def main():
    services_value = os.environ.get("WATCH_SERVICES")
    if not services_value:
        print("[ERROR] WATCH_SERVICES must be set", file=sys.stderr)
        return 1

    if not os.environ.get("COMPOSE_PROJECT_NAME"):
        print("[ERROR] COMPOSE_PROJECT_NAME must be set", file=sys.stderr)
        return 1

    if not os.environ.get("HOST_PWD"):
        print("[ERROR] HOST_PWD must be set", file=sys.stderr)
        return 1

    if not os.environ.get("HOST_HOSTNAME"):
        print("[ERROR] HOST_HOSTNAME must be set", file=sys.stderr)
        return 1

    # deduplicate while preserving order
    services = list(
        dict.fromkeys(service.strip() for service in services_value.split(",") if service.strip())
    )
    if not services:
        print("[ERROR] WATCH_SERVICES must contain at least one container name", file=sys.stderr)
        return 1

    try:
        interval = float(os.environ.get("WATCH_INTERVAL", "30"))
    except ValueError:
        print("[ERROR] WATCH_INTERVAL must be a number", file=sys.stderr)
        return 1
    if interval < 0:
        print("[ERROR] WATCH_INTERVAL must not be negative", file=sys.stderr)
        return 1

    try:
        timeout = float(os.environ.get("RECOVERY_TIMEOUT", "30"))
    except ValueError:
        print("[ERROR] RECOVERY_TIMEOUT must be a number", file=sys.stderr)
        return 1
    if timeout < 0:
        print("[ERROR] RECOVERY_TIMEOUT must not be negative", file=sys.stderr)
        return 1

    signal.signal(signal.SIGTERM, stop_watcher)
    signal.signal(signal.SIGINT, stop_watcher)

    log(f"Compose watcher started (interval={interval:g}s, timeout={timeout:g}s, list of services: {services})")
    write_heartbeat("healthy")

    recovery_needed_at = None

    while True:
        time.sleep(interval)
        # the outer healthcheck treats the watcher as healthy only when the
        # heartbeat file is fresh and contains "healthy"
        watcher_status = "healthy"
        recovery_needed = False

        for service in services:
            container_id = container_id_for_service(service, os.environ["COMPOSE_PROJECT_NAME"], os.environ)
            inspection = inspect_container(container_id, os.environ) if container_id else None
            if inspection is None:
                log(f"[ERROR] {service} container is missing, ambiguous, or cannot be inspected; recovery required", error=True)
                watcher_status = "unhealthy"
                recovery_needed = True
                continue

            running, health = inspection
            debug(f"{service} running={str(running).lower()} health={health}")

            if not running:
                log(f"[ERROR] {service} is not running; recovery required", error=True)
                recovery_needed = True
            elif health == "no-healthcheck":
                log(f"[ERROR] {service} has no health check configured", error=True)
                watcher_status = "unhealthy"
            elif health == "unhealthy":
                log(f"[ERROR] {service} is unhealthy; recovery required", error=True)
                recovery_needed = True

        if recovery_needed:
            if recovery_needed_at is None:
                debug("Recovery needed, starting recovery grace period")
                recovery_needed_at = time.time()
            elif (time.time() - recovery_needed_at) < timeout:
                remaining = timeout - (time.time() - recovery_needed_at)
                debug(f"Recovery still needed, {remaining:.0f}s left in grace period")
            else:
                if not recover(services):
                    watcher_status = "unhealthy"
                recovery_needed_at = None
        else:
            recovery_needed_at = None

        write_heartbeat(watcher_status)


if __name__ == "__main__":
    raise SystemExit(main())
