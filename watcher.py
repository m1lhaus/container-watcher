#!/usr/bin/env python3
"""Container health watcher for Docker Compose projects.

This script periodically inspects a comma-separated list of watched containers
from WATCH_SERVICES. For each service it checks whether the container exists,
whether it is running, and whether it exposes a Docker health status.

If a watched container is missing, not running, or reports "unhealthy", the
watcher marks itself unhealthy and waits for a recovery timeout before running a
full Compose recovery:

    docker compose down && docker compose up -d

The heartbeat file stored at /tmp/watcher.heartbeat is rewritten every cycle with
"healthy" or "unhealthy" so parent container health checks can tell whether the
watcher is currently in a good state.

Required environment variables:
    WATCH_SERVICES - comma-separated list of container names to watch

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

TIMEOUT=int(os.environ.get("RECOVERY_TIMEOUT", "30"))
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
    HEARTBEAT.write_text(f"{status}\n", encoding="ascii")


def inspect_container(name):
    result = subprocess.run(
        ["docker", "inspect", f"--format={DOCKER_INSPECT_FORMAT}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None

    running, health = result.stdout.strip().split("|", 1)
    return running == "true", health


def compose_working_dir():
    pwd = os.environ.get("HOST_PWD", "")
    if pwd:
        pwd = os.path.expanduser(pwd)
        pwd = os.path.abspath(pwd)
        if os.path.isdir(pwd):
            return pwd
    return os.getcwd()


def compose_environment():
    environment = os.environ.copy()
    environment["HOSTNAME"] = os.environ.get("HOST_HOSTNAME", environment.get("HOSTNAME", ""))
    environment["PWD"] = compose_working_dir()
    return environment


def recover():
    log("Running Compose recovery")
    environment = compose_environment()
    down = subprocess.run(["docker", "compose", "down"], cwd=environment["PWD"], env=environment, check=False)
    if down.returncode != 0:
        log("ERROR: docker compose down failed", error=True)
        return False

    up = subprocess.run(["docker", "compose", "up", "-d"], cwd=environment["PWD"], env=environment, check=False)
    if up.returncode != 0:
        log("ERROR: docker compose up -d failed", error=True)
        return False

    log("Compose recovery completed")
    return True


def stop_watcher(signum, _frame):
    log(f"Received signal {signum}; stopping")
    raise SystemExit(0)


def main():
    services_value = os.environ.get("WATCH_SERVICES")
    if not services_value:
        print("ERROR: WATCH_SERVICES must be set", file=sys.stderr)
        return 1

    services = [service.strip() for service in services_value.split(",") if service.strip()]
    if not services:
        print("ERROR: WATCH_SERVICES must contain at least one container name", file=sys.stderr)
        return 1

    try:
        interval = float(os.environ.get("WATCH_INTERVAL", "30"))
    except ValueError:
        print("ERROR: WATCH_INTERVAL must be a number", file=sys.stderr)
        return 1
    if interval < 0:
        print("ERROR: WATCH_INTERVAL must not be negative", file=sys.stderr)
        return 1

    signal.signal(signal.SIGTERM, stop_watcher)
    signal.signal(signal.SIGINT, stop_watcher)

    log(f"Compose watcher started (interval={interval:g}s, list of services: {services})")
    write_heartbeat("healthy")

    recovery_needed_at = None

    while True:
        time.sleep(interval)
        watcher_status = "healthy"
        recovery_needed = False

        for service in services:
            inspection = inspect_container(service)
            if inspection is None:
                log(f"ERROR: {service} does not exist; recovery required", error=True)
                recovery_needed = True
                continue

            running, health = inspection
            debug(f"{service} running={str(running).lower()} health={health}")

            if not running:
                log(f"ERROR: {service} is not running; recovery required", error=True)
                recovery_needed = True
            elif health == "no-healthcheck":
                log(f"ERROR: {service} has no health check configured", error=True)
                watcher_status = "unhealthy"
            elif health == "unhealthy":
                log(f"{service} is unhealthy; recovery required")
                recovery_needed = True

        write_heartbeat(watcher_status)
        if recovery_needed:
            if recovery_needed_at is None:
                debug(f"Recovery needed, postponing recovery until timeout")
                recovery_needed_at = time.time()
            elif (time.time() - recovery_needed_at) < TIMEOUT:
                debug(f"Recovery needed, postponing recovery until timeout")
                continue    # avoid premature recovery (e.g. due to currently running compose down and up) 
            else:
                recover()
                recovery_needed_at = None
        else:
            recovery_needed_at = None


if __name__ == "__main__":
    raise SystemExit(main())
