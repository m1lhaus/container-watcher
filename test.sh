#!/bin/sh
set -eu

IMAGE_NAME=container-watcher:test
CONTAINER_NAME=container-watcher-test

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

ENV_FILE="$SCRIPT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    printf 'Missing %s. Copy .env.example to .env and edit it first.\n' "$ENV_FILE" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1091
. "$ENV_FILE"
set +a

: "${IMAGE_NAME:?IMAGE_NAME must be set in .env}"
: "${CONTAINER_NAME:?CONTAINER_NAME must be set in .env}"
: "${HOST_PWD:?HOST_PWD must be set in .env}"
: "${HOST_HOSTNAME:?HOST_HOSTNAME must be set in .env}"
: "${COMPOSE_PROJECT_NAME:?COMPOSE_PROJECT_NAME must be set in .env}"
: "${WATCH_SERVICES:?WATCH_SERVICES must be set in .env}"
: "${WATCH_INTERVAL:?WATCH_INTERVAL must be set in .env}"
: "${RECOVERY_TIMEOUT:?RECOVERY_TIMEOUT must be set in .env}"
: "${DEBUG:?DEBUG must be set in .env}"

if [ ! -d "$HOST_PWD" ]; then
    printf 'HOST_PWD does not exist: %s\n' "$HOST_PWD" >&2
    printf 'Set HOST_PWD to the directory containing the Compose project.\n' >&2
    exit 1
fi

cleanup() {
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM

echo "Building $IMAGE_NAME"
docker build --tag "$IMAGE_NAME" "$SCRIPT_DIR"

echo "Running $IMAGE_NAME"
echo "Press Ctrl-C to stop the test container."

docker run \
    --name "$CONTAINER_NAME" \
    --rm \
    --init \
    --workdir /workspace \
    --env-file "$ENV_FILE" \
    --volume /var/run/docker.sock:/var/run/docker.sock \
    --volume "$HOST_PWD:/workspace" \
    "$IMAGE_NAME"
