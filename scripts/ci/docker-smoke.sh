#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:-agent-env-pool:smoke}"
NAME="${AGENT_ENV_POOL_SMOKE_NAME:-agent-env-pool-smoke-${GITHUB_RUN_ID:-local}-$$}"
TIMEOUT_SECONDS="${AGENT_ENV_POOL_SMOKE_TIMEOUT_SECONDS:-60}"

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
}

trap cleanup EXIT

docker rm -f "$NAME" >/dev/null 2>&1 || true

docker run -d \
  --name "$NAME" \
  -p 127.0.0.1::8100 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --add-host host.docker.internal:host-gateway \
  -e AGENT_ENV_POOL_DOCKER_READY_HOST=host.docker.internal \
  "$IMAGE" >/dev/null

HOST_PORT="$(docker inspect -f '{{(index (index .NetworkSettings.Ports "8100/tcp") 0).HostPort}}' "$NAME")"
BASE_URL="http://127.0.0.1:${HOST_PORT}"

deadline=$((SECONDS + TIMEOUT_SECONDS))
until curl -fsS "$BASE_URL/api/v1/servers" >/dev/null; do
  if (( SECONDS >= deadline )); then
    echo "agent-env-pool smoke check failed: $BASE_URL did not become ready" >&2
    docker logs "$NAME" >&2 || true
    exit 1
  fi
  sleep 1
done

echo "agent-env-pool smoke check passed: $BASE_URL"
