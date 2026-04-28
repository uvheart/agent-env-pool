<p align="center">
  <img src="./pic/logo.png" alt="agent-env-pool" width="120" />
</p>

<h1 align="center">agent-env-pool</h1>

<p align="center">
  A lightweight single-node Docker sandbox pool for Agent RL rollout.
</p>

<p align="center">
  <a href="https://hub.docker.com/r/uvheart280/agent-env-pool"><img src="https://img.shields.io/docker/v/uvheart280/agent-env-pool?label=docker&color=blue" alt="Docker" /></a>
  <a href="./README.zh.md">中文文档</a>
</p>

<p align="center">
  <img src="./pic/index.png" alt="agent-env-pool overview" width="800" />
</p>

---

`agent-env-pool` does one thing well: reliably boot, track, and release Docker sandboxes so researchers spend less time wiring up Docker endpoints, ports, quota, and cleanup.

## Installation

### Option A — Docker (recommended)

No Python setup needed. Requires Docker.

```bash
docker run -d \
  --name agent-env-pool \
  -p 8100:8100 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --add-host host.docker.internal:host-gateway \
  -e AGENT_ENV_POOL_DOCKER_READY_HOST=host.docker.internal \
  uvheart280/agent-env-pool:latest
```

Verify:

```bash
curl http://127.0.0.1:8100/api/v1/servers
# {"meta":{"trace_id":"..."},"data":{"items":[],"total":0}}
```

### Option B — From source

```bash
git clone https://github.com/uvheart/agent-env-pool.git
cd agent-env-pool

conda create -n agent-env-pool python=3.11 -y
conda activate agent-env-pool

pip install -r requirements.txt

python -m agent_env_pool --host 0.0.0.0 --port 8100
```

## Quickstart

> **Prerequisites:** `agent-env-pool` is the scheduling layer only. Your sandbox image must already exist on the host.

### Step 1 — Pull the browser sandbox image

```bash
docker pull zenika/alpine-chrome:124
```

> Build `browser-use-chrome` yourself or use any image that exposes a CDP port on `9223`.

### Step 2 — Boot a browser sandbox

```bash
SERVER=$(curl -s -X POST http://127.0.0.1:8100/api/v1/servers/boot \
  -H 'content-type: application/json' \
  -d '{
    "env_type": "browser-use",
    "image": "zenika/alpine-chrome:124",
    "endpoints": [
      {"name": "cdp", "container_port": 9222, "protocol": "cdp", "ready_check": {"type": "cdp"}}
    ],
    "metadata": {
      "command": [
        "--no-sandbox",
        "--remote-debugging-address=0.0.0.0",
        "--remote-debugging-port=9222",
        "about:blank"
      ]
    }
  }')

echo $SERVER
# Returns server_id, cdp_url, status, endpoints
```

### Step 3 — Run the E2E test

The E2E test boots a browser sandbox, connects via CDP, navigates to Baidu, captures a screenshot, verifies the API, and shuts down cleanly.

```bash
# Install test dependencies (Option B only; Docker users still need these locally)
pip install pytest pytest-asyncio

python -m pytest tests/test_e2e_browser.py -v -s
```

Expected output:

```
[BOOT]       server_id=...
[BOOT]       cdp_url=http://127.0.0.1:XXXXX
[CDP]        browser ready: Chrome/...
[CDP]        ws_url=ws://127.0.0.1:XXXXX/devtools/browser/...
[SCREENSHOT] saved tests/screenshot_baidu.png (87.x KB)
[SHUTDOWN]   {'message': 'success'}
PASSED
```

Screenshot is saved to `tests/screenshot_baidu.png`.

### Step 4 — Shut it down

```bash
SERVER_ID=$(echo $SERVER | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['server_id'])")
curl -s -X POST "http://127.0.0.1:8100/api/v1/servers/$SERVER_ID/shutdown?force=true"
```

## API

### Boot a sandbox

```bash
curl -X POST http://127.0.0.1:8100/api/v1/servers/boot \
  -H 'content-type: application/json' \
  -d '{
    "env_type": "browser-use",
    "image": "zenika/alpine-chrome:124",
    "endpoints": [
      {"name": "cdp", "container_port": 9222, "protocol": "cdp", "ready_check": {"type": "cdp"}}
    ]
  }'
```

Response fields: `server_id`, `status`, `cdp_url`, `endpoints[].host_port`, `endpoints[].url`.

Custom image with HTTP + TCP endpoints:

```json
{
  "env_type": "custom",
  "image": "my-sandbox:latest",
  "endpoints": [
    {"name": "api", "container_port": 8080, "protocol": "http", "ready_check": {"type": "http", "path": "/health"}},
    {"name": "stream", "container_port": 9000, "protocol": "tcp", "ready_check": {"type": "tcp"}}
  ]
}
```

### Acquire & Release (pool reuse)

```bash
# Reuses an idle sandbox if available, otherwise boots a new one
curl -X POST http://127.0.0.1:8100/api/v1/pool/acquire \
  -H 'content-type: application/json' \
  -d '{"env_type":"browser-use","image":"zenika/alpine-chrome:124","endpoints":[{"name":"cdp","container_port":9223,"protocol":"cdp","ready_check":{"type":"cdp"}}]}'

# Release back to the idle pool
curl -X POST http://127.0.0.1:8100/api/v1/pool/release/$SERVER_ID
```

### Batch rollout

```bash
curl -X POST http://127.0.0.1:8100/api/v1/rollout/boot \
  -H 'content-type: application/json' \
  -d '{
    "count": 4,
    "env_type": "browser-use",
    "image": "zenika/alpine-chrome:124",
    "endpoints": [{"name": "cdp", "container_port": 9222, "protocol": "cdp", "ready_check": {"type": "cdp"}}]
  }'

curl http://127.0.0.1:8100/api/v1/rollout/$ROLLOUT_ID
curl -X POST "http://127.0.0.1:8100/api/v1/rollout/$ROLLOUT_ID/shutdown?force=true"
```

### List, inspect & logs

```bash
curl http://127.0.0.1:8100/api/v1/servers
curl http://127.0.0.1:8100/api/v1/servers/$SERVER_ID
curl "http://127.0.0.1:8100/api/v1/servers/$SERVER_ID/logs?tail=200"
```

All responses are wrapped with a trace ID:

```json
{"meta": {"trace_id": "..."}, "data": {}}
```

## Python SDK

```python
from agent_env_pool import EnvPoolClient

pool = EnvPoolClient("http://127.0.0.1:8100")

with pool.acquire(endpoints=[
    {"name": "cdp", "container_port": 9222, "protocol": "cdp", "ready_check": {"type": "cdp"}}
]) as env:
    print(env.server_id)
    print(env.cdp_url)   # connect your agent here
```

## Use Cases

| Sandbox type | Image | Endpoint |
|---|---|---|
| Chrome / browser automation | `zenika/alpine-chrome:124` | CDP on `9223` |
| Playwright / scraping | any | HTTP or WebSocket |
| VS Code / code-server | `codercom/code-server` | HTTP |
| VNC desktop | `dorowu/ubuntu-desktop-lxde-vnc` | TCP / WebSocket |
| Agent CLI (Claude Code, Codex, Gemini, Qwen, Kimi) | custom | API / stream port |
| RL training workers | custom | any |

## Lifecycle

```
starting → running → occupied → stopping → stopped
              ↘          ↘          ↘
                        error
```

`starting`, `running`, `occupied`, `stopping` all count against the active quota. Quota is enforced with a SQLite `BEGIN IMMEDIATE` transaction before Docker boot begins.

## Roadmap

| Version | Focus |
|---|---|
| V0.1 | Single-node Docker sandbox pool ✅ |
| V0.2 | Trajectory collection & JSONL export |
| V0.3 | Failure replay & browser action adapters |
| V0.4 | Monitoring UI & operator tooling |
| V0.5 | Kubernetes & distributed scheduling |
