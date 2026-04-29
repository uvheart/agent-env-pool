<p align="center">
  <img src="./pic/logo.png" alt="agent-env-pool" width="120" />
</p>

<h1 align="center">agent-env-pool</h1>

<p align="center">
  轻量级单节点 Docker 沙箱池，专为 Agent RL rollout 设计。
</p>

<p align="center">
  <a href="https://hub.docker.com/r/uvheart280/agent-env-pool"><img src="https://img.shields.io/docker/v/uvheart280/agent-env-pool?label=docker&color=blue" alt="Docker" /></a>
  <a href="./README.md">English</a>
</p>

<p align="center">
  <img src="./pic/index.png" alt="agent-env-pool overview" width="800" />
</p>

---

`agent-env-pool` 只做一件事：可靠地启动、追踪和释放 Docker 沙箱，让研究者不再需要手动处理 Docker 端点、端口、配额和清理。

## 安装

### 方案一：Docker 启动（推荐）

无需 Python 环境，仅需 Docker。

```bash
docker run -d \
  --name agent-env-pool \
  -p 8100:8100 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --add-host host.docker.internal:host-gateway \
  -e AGENT_ENV_POOL_DOCKER_READY_HOST=host.docker.internal \
  uvheart280/agent-env-pool:latest
```

> 国内如果拉取超时，请先为 Docker 配置代理或镜像加速器。

验证服务已启动：

```bash
curl http://127.0.0.1:8100/api/v1/servers
# {"meta":{"trace_id":"..."},"data":{"items":[],"total":0}}
```

### 方案二：源码安装

```bash
git clone https://github.com/uvheart/agent-env-pool.git
cd agent-env-pool

conda create -n agent-env-pool python=3.11 -y
conda activate agent-env-pool

pip install -r requirements.txt

python -m agent_env_pool --host 0.0.0.0 --port 8100
```

## 快速开始

> **前提**：`agent-env-pool` 只是调度层，沙箱镜像需要预先拉取到宿主机。

### 第一步：拉取浏览器沙箱镜像

```bash
docker pull zenika/alpine-chrome:124
```

> `zenika/alpine-chrome:124` 可通过下面的命令在 `9222` 端口暴露 Chrome DevTools Protocol。你也可以使用任何自定义 CDP 镜像。

### 第二步：启动一个浏览器沙箱

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
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--remote-debugging-address=0.0.0.0",
        "--remote-debugging-port=9222",
        "about:blank"
      ]
    }
  }')

echo $SERVER
# 返回 server_id、cdp_url、status、endpoints
```

### 第三步：运行 E2E 测试

E2E 测试会自动完成：启动浏览器沙箱 → CDP 连接 → 渲染本地测试页面 → 截图保存 → 验证 API → 关闭沙箱。

```bash
# 安装测试依赖（方案二用户；方案一用户也需在本地执行）
pip install pytest pytest-asyncio

python -m pytest tests/test_e2e_browser.py -v -s
```

预期输出：

```
[BOOT]       server_id=...
[BOOT]       cdp_url=http://127.0.0.1:XXXXX
[CDP]        browser ready: Chrome/...
[CDP]        ws_url=ws://127.0.0.1:XXXXX/devtools/browser/...
[SCREENSHOT] saved tests/screenshot_baidu.png (87.x KB)
[SHUTDOWN]   {'message': 'success'}
PASSED
```

截图保存在 `tests/screenshot_baidu.png`。

### 第四步：关闭沙箱

```bash
SERVER_ID=$(echo $SERVER | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['server_id'])")
curl -s -X POST "http://127.0.0.1:8100/api/v1/servers/$SERVER_ID/shutdown?force=true"
```

## API

### 启动沙箱

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

响应字段：`server_id`、`status`、`cdp_url`、`endpoints[].host_port`、`endpoints[].url`。

自定义镜像（HTTP + TCP 端点）示例：

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

### 获取与释放（池化复用）

```bash
# 优先复用空闲沙箱，无可用时自动启动新沙箱
curl -X POST http://127.0.0.1:8100/api/v1/pool/acquire \
  -H 'content-type: application/json' \
  -d '{"env_type":"browser-use","image":"zenika/alpine-chrome:124","endpoints":[{"name":"cdp","container_port":9222,"protocol":"cdp","ready_check":{"type":"cdp"}}],"metadata":{"command":["--no-sandbox","--remote-debugging-address=0.0.0.0","--remote-debugging-port=9222","about:blank"]}}'

# 用完释放回空闲池
curl -X POST http://127.0.0.1:8100/api/v1/pool/release/$SERVER_ID
```

### 批量 Rollout

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

### 列表、详情与日志

```bash
curl http://127.0.0.1:8100/api/v1/servers
curl http://127.0.0.1:8100/api/v1/servers/$SERVER_ID
curl "http://127.0.0.1:8100/api/v1/servers/$SERVER_ID/logs?tail=200"
```

所有响应均包含请求追踪：

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
    print(env.cdp_url)   # 在此连接你的 Agent
```

## 适用场景

| 沙箱类型 | 镜像示例 | 端点 |
|---|---|---|
| Chrome / 浏览器自动化 | `zenika/alpine-chrome:124` | CDP on `9222` |
| Playwright / 爬虫 | 任意 | HTTP 或 WebSocket |
| VS Code / code-server | `codercom/code-server` | HTTP |
| VNC 桌面 | `dorowu/ubuntu-desktop-lxde-vnc` | TCP / WebSocket |
| Agent CLI（Claude Code、Codex、Gemini CLI、Qwen Code、Kimi CLI） | 自定义 | API / 流端口 |
| RL 训练 Worker | 自定义 | 任意 |

## 沙箱生命周期

```
starting → running → occupied → stopping → stopped
              ↘          ↘          ↘
                        error
```

`starting`、`running`、`occupied`、`stopping` 均占用配额。配额通过 SQLite `BEGIN IMMEDIATE` 事务在 Docker 启动前原子检查并写入。

## 路线图

| 版本 | 目标 |
|---|---|
| V0.1 | 单节点 Docker 沙箱池 ✅ |
| V0.2 | 轨迹收集与 JSONL 导出 |
| V0.3 | 失败回放与浏览器动作适配器 |
| V0.4 | 监控 UI 与运维工具 |
| V0.5 | Kubernetes 与分布式调度 |
