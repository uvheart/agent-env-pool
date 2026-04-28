# agent-env-pool

`agent-env-pool` is a lightweight single-node Docker sandbox pool for Agent RL rollout.

It focuses on one thing for V0.1: reliably creating, tracking, and releasing Docker sandboxes so researchers can spend less time on Docker endpoints, ports, quota, and cleanup.

## Quickstart

**No build needed.** Create a `docker-compose.yml` and start:

```yaml
services:
  agent-env-pool:
    image: uvheart/agent-env-pool:latest
    ports:
      - "8100:8100"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./data:/app/data
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      AGENT_ENV_POOL_DATABASE_URL: "sqlite+aiosqlite:///./data/agent_env_pool.db"
      AGENT_ENV_POOL_DOCKER_BROWSER_IMAGE: "browser-use-chrome:latest"
      AGENT_ENV_POOL_DOCKER_BROWSER_PORT: "9223"
      AGENT_ENV_POOL_PUBLIC_HOST: "127.0.0.1"
      AGENT_ENV_POOL_DOCKER_READY_HOST: "host.docker.internal"
```

```bash
docker compose up -d
```

Boot one browser sandbox:

```bash
curl -s -X POST http://127.0.0.1:8100/api/v1/servers/boot -H 'content-type: application/json' -d '{}'
```

Boot 10 browser sandboxes for rollout:

```bash
curl -s -X POST http://127.0.0.1:8100/api/v1/rollout/boot -H 'content-type: application/json' -d '{"count":10}'
```

The response returns Docker-assigned endpoint URLs, for example `data.cdp_url` and `data.endpoints[0].url`. The default browser image is `browser-use-chrome:latest`, and its CDP endpoint is expected on container port `9223`.

## V0.1 Scope

- Start a Docker sandbox.
- Expose one or more container endpoints with Docker-allocated host ports.
- Support endpoint protocol labels including `cdp`, `http`, `sse`, `ws`, and `tcp`.
- Run basic endpoint readiness checks: `none`, `tcp`, `http`, or `cdp`.
- Enforce a SQLite-backed active sandbox quota.
- Track sandbox lifecycle state.
- Boot sandboxes in batches for rollout.
- Shutdown individual sandboxes or whole rollouts.
- Include `trace_id` in logs and JSON responses.
- Provide an end-to-end CDP test that exposes an endpoint, opens Baidu, and captures a screenshot.
- Provide a minimal Python SDK for rollout workers.

This release is intentionally single-node first. Kubernetes, Redis queues, Postgres, trajectory collection, replay, reward evaluation, and web workbench features are later-stage concerns.

## Requirements

- Python 3.11+
- Docker daemon available to the service
- A Docker image that exposes the ports you declare in `endpoints`

The E2E test expects `browser-use-chrome:latest`, whose CDP proxy listens on container port `9223`.

## Local Python Install

```bash
pip install -r requirements.txt
```

## Configure

Copy `.env.example` if you want local overrides:

```bash
cp .env.example .env
```

Common settings:

```bash
export AGENT_ENV_POOL_DOCKER_BROWSER_IMAGE=browser-use-chrome:latest
export AGENT_ENV_POOL_DOCKER_BROWSER_PORT=9223
export AGENT_ENV_POOL_MAX_POOL_SIZE=32
export AGENT_ENV_POOL_PUBLIC_HOST=127.0.0.1
```

## Local Python Run

```bash
python -m agent_env_pool --host 0.0.0.0 --port 8100
```

Health check by listing active sandboxes:

```bash
curl http://127.0.0.1:8100/api/v1/servers
```

All JSON responses are wrapped with a request trace:

```json
{
  "meta": {"trace_id": "..."},
  "data": {}
}
```

## API

### Boot One Sandbox

```bash
curl -X POST http://127.0.0.1:8100/api/v1/servers/boot \
  -H 'content-type: application/json' \
  -d '{
    "env_type": "browser-use",
    "runtime": "docker",
    "endpoints": [
      {
        "name": "cdp",
        "container_port": 9223,
        "protocol": "cdp",
        "ready_check": {"type": "cdp"}
      }
    ]
  }'
```

The response includes:

- `server_id`
- `status`
- `endpoints`, each with `host`, Docker-assigned `host_port`, and direct `url`
- `cdp_url` for compatibility when an endpoint uses protocol `cdp`
- Docker `resource_id`
- Docker-assigned primary host `port`

Example for a non-browser image with HTTP and TCP endpoints:

```json
{
  "env_type": "custom",
  "runtime": "docker",
  "image": "my-sandbox:latest",
  "endpoints": [
    {
      "name": "api",
      "container_port": 8080,
      "protocol": "http",
      "ready_check": {"type": "http", "path": "/health", "expected_status": 200}
    },
    {
      "name": "worker-stream",
      "container_port": 9000,
      "protocol": "tcp",
      "ready_check": {"type": "tcp"}
    }
  ]
}
```

### Acquire And Release

Use this flow for rollout workers. `acquire` reuses an idle sandbox if available, otherwise it boots a new one.

```bash
curl -X POST http://127.0.0.1:8100/api/v1/pool/acquire \
  -H 'content-type: application/json' \
  -d '{"env_type":"browser-use","runtime":"docker","endpoints":[{"name":"cdp","container_port":9223,"protocol":"cdp","ready_check":{"type":"cdp"}}]}'
```

Release it back to the idle pool:

```bash
curl -X POST http://127.0.0.1:8100/api/v1/pool/release/$SERVER_ID
```

### Shutdown

```bash
curl -X POST 'http://127.0.0.1:8100/api/v1/servers/$SERVER_ID/shutdown?force=true'
```

### List And Inspect

```bash
curl http://127.0.0.1:8100/api/v1/servers
curl http://127.0.0.1:8100/api/v1/servers/$SERVER_ID
curl 'http://127.0.0.1:8100/api/v1/servers/$SERVER_ID/logs?tail=200'
```

### Batch Rollout Boot

```bash
curl -X POST http://127.0.0.1:8100/api/v1/rollout/boot \
  -H 'content-type: application/json' \
  -d '{
    "count": 4,
    "env_type": "browser-use",
    "runtime": "docker",
    "endpoints": [
      {"name": "cdp", "container_port": 9223, "protocol": "cdp", "ready_check": {"type": "cdp"}}
    ]
  }'
```

Inspect and cleanup a rollout:

```bash
curl http://127.0.0.1:8100/api/v1/rollout/$ROLLOUT_ID
curl -X POST 'http://127.0.0.1:8100/api/v1/rollout/$ROLLOUT_ID/shutdown?force=true'
```

## Inspired Use Cases

The scenarios in [OpenSandbox examples](https://github.com/alibaba/OpenSandbox/tree/main/examples) are useful references for what people run inside sandboxes, but those examples are not drop-in compatible with this project. Many of them depend on OpenSandbox-specific SDKs, gateway/domain routing, Kubernetes, PVC, or volume abstractions.

For `agent-env-pool` V0.1, the adaptation path is: package the workload as a Docker image, declare its exposed endpoints, then let this service boot it, publish ports, return direct URLs, track quota, and clean it up.

Good scenario fits:

- **Chrome / browser automation**: expose a CDP endpoint such as `9223`, return `cdp_url`, and let rollout workers connect directly.
- **Playwright / scraping services**: expose an HTTP or WebSocket control endpoint and use `http`, `ws`, or `tcp` readiness checks.
- **VS Code / code-server**: expose the web UI port as an `http` endpoint.
- **Desktop / VNC-style sandboxes**: expose the VNC or noVNC port as `tcp`, `ws`, or `http`, depending on the image.
- **Agent CLI sandboxes** such as Claude Code, Codex CLI, Gemini CLI, Qwen Code, or Kimi CLI: run them inside a Docker image and expose any API, stream, or control port they provide.
- **RL training workers**: batch boot containers with `/rollout/boot`, use SQLite quota to cap active sandboxes, and consume endpoint URLs from the response.

What V0.1 does not try to copy yet:

- Kubernetes runtime and PVC abstractions.
- Built-in volume mount APIs.
- Hosted gateway/domain routing.
- Multi-tenant auth and remote cluster scheduling.
- Trajectory recording, replay, and reward evaluation.

So the current boundary is simple: if an example can run as a Docker container and its useful ports are known, `agent-env-pool` can boot it, publish those ports, return direct URLs, track status, and clean it up.

## Python SDK

```python
from agent_env_pool import EnvPoolClient

pool = EnvPoolClient("http://127.0.0.1:8100")

with pool.acquire(endpoints=[
    {
        "name": "cdp",
        "container_port": 9223,
        "protocol": "cdp",
        "ready_check": {"type": "cdp"},
    }
]) as env:
    print(env.server_id)
    print(env.cdp_url)
    print(env.endpoints)
    # Run your agent against env.cdp_url.
```

Cold shutdown instead of release:

```python
env = pool.acquire(endpoints=[{"name": "cdp", "container_port": 9223, "protocol": "cdp"}])
env.shutdown(force=True)
```

## E2E Test

The E2E test boots a browser sandbox, connects to CDP, opens Baidu, captures a screenshot, verifies list/detail APIs, and shuts the sandbox down.

```bash
python -m pytest tests/test_e2e_browser.py -v -s
```

## Lifecycle

```text
starting -> running -> occupied -> stopping -> stopped
       \        \           \
                error
```

Active states occupy quota:

```text
starting, running, occupied, stopping
```

Quota is enforced with a short SQLite `BEGIN IMMEDIATE` transaction that atomically checks the active count and inserts a `starting` record before Docker boot begins.

## Roadmap

