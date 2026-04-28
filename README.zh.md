# agent-env-pool

`agent-env-pool` 是一个轻量级单节点 Docker 沙箱池，专为 Agent RL rollout 设计。

V0.1 只做一件事：可靠地创建、追踪和释放 Docker 沙箱，让研究者不再需要手动处理 Docker 端点、端口、配额和清理工作。

## 快速开始

**无需本地构建**，直接拉取预构建镜像启动服务：

```bash
curl -O https://raw.githubusercontent.com/uvheart/agent-env-pool/main/docker-compose.yml
docker compose up -d
```

### 启动一个浏览器沙箱

```bash
curl -s -X POST http://127.0.0.1:8100/api/v1/servers/boot \
  -H 'content-type: application/json' -d '{}'
```

### 批量启动 10 个浏览器沙箱（用于 rollout）

```bash
curl -s -X POST http://127.0.0.1:8100/api/v1/rollout/boot \
  -H 'content-type: application/json' -d '{"count":10}'
```

响应中包含 Docker 分配的端点 URL，例如 `data.cdp_url` 和 `data.endpoints[0].url`。默认浏览器镜像为 `browser-use-chrome:latest`，其 CDP 端点监听在容器端口 `9223`。

## V0.1 功能范围

- 启动 Docker 沙箱
- 暴露一个或多个容器端点，使用 Docker 动态分配的宿主机端口
- 支持端点协议标签：`cdp`、`http`、`sse`、`ws`、`tcp`
- 支持端点就绪检查：`none`、`tcp`、`http`、`cdp`
- 基于 SQLite 的活跃沙箱配额管理
- 沙箱生命周期状态追踪
- 支持批量启动沙箱（rollout）
- 支持单个沙箱或整批 rollout 的关闭
- 日志和 JSON 响应中均包含 `trace_id`
- 提供端到端 CDP 测试：启动端点、打开百度、截图
- 提供最小化 Python SDK 供 rollout worker 使用

本版本有意以单节点优先。Kubernetes、Redis 队列、Postgres、轨迹收集、回放、奖励评估和 Web 工作台等功能属于后续阶段。

## 环境要求

- Python 3.11+
- Docker daemon 对服务可用
- 一个暴露了 `endpoints` 中声明端口的 Docker 镜像

E2E 测试依赖 `browser-use-chrome:latest`，其 CDP 代理监听在容器端口 `9223`。

## 本地 Python 安装

```bash
pip install -r requirements.txt
```

## 配置

复制 `.env.example` 作为本地配置：

```bash
cp .env.example .env
```

常用配置项：

```bash
export AGENT_ENV_POOL_DOCKER_BROWSER_IMAGE=browser-use-chrome:latest
export AGENT_ENV_POOL_DOCKER_BROWSER_PORT=9223
export AGENT_ENV_POOL_MAX_POOL_SIZE=32
export AGENT_ENV_POOL_PUBLIC_HOST=127.0.0.1
```

## 本地 Python 运行

```bash
python -m agent_env_pool --host 0.0.0.0 --port 8100
```

列出活跃沙箱（健康检查）：

```bash
curl http://127.0.0.1:8100/api/v1/servers
```

所有 JSON 响应均包含请求追踪信息：

```json
{
  "meta": {"trace_id": "..."},
  "data": {}
}
```

## API

### 启动单个沙箱

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

响应包含：

- `server_id`
- `status`
- `endpoints`：每个端点含 `host`、Docker 分配的 `host_port` 和直连 `url`
- `cdp_url`：当端点协议为 `cdp` 时的快捷字段
- Docker `resource_id`
- Docker 分配的主端口 `port`

自定义镜像示例（HTTP + TCP 端点）：

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

### 获取与释放（池化复用）

适用于 rollout worker 场景。`acquire` 优先复用空闲沙箱，无可用时自动启动新沙箱。

```bash
curl -X POST http://127.0.0.1:8100/api/v1/pool/acquire \
  -H 'content-type: application/json' \
  -d '{"env_type":"browser-use","runtime":"docker","endpoints":[{"name":"cdp","container_port":9223,"protocol":"cdp","ready_check":{"type":"cdp"}}]}'
```

用完释放回空闲池：

```bash
curl -X POST http://127.0.0.1:8100/api/v1/pool/release/$SERVER_ID
```

### 关闭沙箱

```bash
curl -X POST 'http://127.0.0.1:8100/api/v1/servers/$SERVER_ID/shutdown?force=true'
```

### 列表与详情查询

```bash
curl http://127.0.0.1:8100/api/v1/servers
curl http://127.0.0.1:8100/api/v1/servers/$SERVER_ID
curl 'http://127.0.0.1:8100/api/v1/servers/$SERVER_ID/logs?tail=200'
```

### 批量 Rollout 启动

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

查询和清理 rollout：

```bash
curl http://127.0.0.1:8100/api/v1/rollout/$ROLLOUT_ID
curl -X POST 'http://127.0.0.1:8100/api/v1/rollout/$ROLLOUT_ID/shutdown?force=true'
```

## 适用场景

以下场景均可将工作负载打包为 Docker 镜像，声明暴露的端点，由本服务负责启动、端口发布、URL 返回、配额管理和清理：

- **Chrome / 浏览器自动化**：暴露 CDP 端点（如 `9223`），返回 `cdp_url`，供 rollout worker 直连
- **Playwright / 爬虫服务**：暴露 HTTP 或 WebSocket 控制端点，使用 `http`、`ws` 或 `tcp` 就绪检查
- **VS Code / code-server**：将 Web UI 端口作为 `http` 端点暴露
- **桌面 / VNC 类沙箱**：将 VNC 或 noVNC 端口作为 `tcp`、`ws` 或 `http` 暴露
- **Agent CLI 沙箱**（Claude Code、Codex CLI、Gemini CLI、Qwen Code、Kimi CLI 等）：在 Docker 镜像中运行，暴露其 API、流或控制端口
- **RL 训练 Worker**：批量启动容器（`/rollout/boot`），用 SQLite 配额限制活跃沙箱数，从响应中消费端点 URL

V0.1 暂不涉及：Kubernetes 运行时、内置 Volume 挂载 API、托管网关/域名路由、多租户鉴权、轨迹录制与回放、奖励评估。

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
    # 使用 env.cdp_url 运行你的 Agent
```

直接关闭而不释放回池：

```python
env = pool.acquire(endpoints=[{"name": "cdp", "container_port": 9223, "protocol": "cdp"}])
env.shutdown(force=True)
```

## E2E 测试

E2E 测试会启动一个浏览器沙箱，通过 CDP 连接，打开百度，截图，验证列表/详情 API，最后关闭沙箱。

```bash
python -m pytest tests/test_e2e_browser.py -v -s
```

## 沙箱生命周期

```text
starting -> running -> occupied -> stopping -> stopped
       \        \           \
                error
```

占用配额的活跃状态：

```text
starting、running、occupied、stopping
```

配额通过 SQLite `BEGIN IMMEDIATE` 事务原子执行：在 Docker 启动前，先检查活跃数量并插入 `starting` 记录，保证并发安全。

## 路线图

- V0.2：轨迹收集与 JSONL 导出
- V0.3：失败回放与浏览器动作适配器
- V0.4：可选监控 UI 与更丰富的运维工具
- V0.5：Kubernetes 与分布式调度
