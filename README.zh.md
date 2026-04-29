<p align="center">
  <img src="./pic/logo.png" alt="agent-env-pool" width="120" />
</p>

<h1 align="center">agent-env-pool</h1>

<p align="center">
  一个 API 批量管理 Docker 容器 — 一键启动数百个沙箱，自动追踪，自动清理。
</p>

<p align="center">
  <a href="https://hub.docker.com/r/uvheart280/agent-env-pool"><img src="https://img.shields.io/docker/v/uvheart280/agent-env-pool?label=docker&color=blue" alt="Docker" /></a>
</p>
<p align="center">
   <a href="./README.md">English</a>
</p>

<p align="center">
  <img src="./pic/index.png" alt="agent-env-pool overview" width="800" />
</p>

---

## 为什么需要 agent-env-pool？

跑 10 个 Docker 容器很简单。但同时跑 200 个 — 每个镜像不同、端口不同、健康检查不同、生命周期不同 — 就是另一回事了。`agent-env-pool` 把这件事变成一次 API 调用。

**核心优势：**

- **任意镜像** — Chrome、Playwright、code-server、VNC 桌面、ML Worker、Agent CLI — 只要能跑在 Docker 里，就能被管理
- **批量启动** — 一个请求启动 N 个容器，自动配额管控、自动端口分配
- **健康检查感知** — 内置 CDP / HTTP / TCP 就绪探针，API 返回 "running" 时容器已经可用
- **自动清理** — 孤儿容器检测、强制关闭、按 rollout 批量回收
- **零配置网络** — Docker 自动分配临时宿主端口，无需手动映射
- **池化复用** — acquire/release 模式让热容器保持待命，适合长时间工作负载
- **全链路追踪** — 每个 API 响应都带 `trace_id`，方便排查

## 适用场景

| 场景 | 镜像示例 | 端点 | 用 agent-env-pool 的好处 |
|---|---|---|---|
| 大规模浏览器自动化 | `zenika/alpine-chrome:124` | CDP `:9222` | 一键启动 50 个 Chrome，每个都有 CDP 就绪检查，自动清理 |
| AI Agent 沙箱 | Claude Code / Codex / Gemini CLI | API / 流 | 每个 Agent 会话一个隔离环境，配额管控 |
| RL 训练 Rollout | 自定义训练镜像 | 任意 | 批量启动训练 Worker，跟踪生命周期，收集结果 |
| Playwright / 爬虫集群 | 任意无头浏览器 | HTTP / WS | 跨任务池化复用，无端口冲突 |
| Code-server / 开发环境 | `codercom/code-server` | HTTP `:8080` | 按需创建开发环境，自带健康检查 |
| VNC 远程桌面 | `dorowu/ubuntu-desktop-lxde-vnc` | TCP / WS | 批量桌面供应，自动端口分配 |
| CI/CD 测试矩阵 | 任意测试镜像 | 各种 | 并行启动测试环境，完成后自动回收 |

## 安装

### 方案一：Docker 启动（推荐）

```bash
docker run -d \
  --name agent-env-pool \
  -p 127.0.0.1:8100:8100 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --add-host host.docker.internal:host-gateway \
  -e AGENT_ENV_POOL_DOCKER_READY_HOST=host.docker.internal \
  uvheart280/agent-env-pool:latest

export AGENT_ENV_POOL_URL=http://127.0.0.1:8100
```

> 国内拉取超时请先配置 Docker 镜像加速器。

在共享主机上 `8100` 可能被占用，改用随机端口：

```bash
docker run -d \
  --name agent-env-pool \
  -p 127.0.0.1::8100 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --add-host host.docker.internal:host-gateway \
  -e AGENT_ENV_POOL_DOCKER_READY_HOST=host.docker.internal \
  uvheart280/agent-env-pool:latest

export AGENT_ENV_POOL_URL=http://127.0.0.1:$(docker inspect -f '{{(index (index .NetworkSettings.Ports "8100/tcp") 0).HostPort}}' agent-env-pool)
```

验证：

```bash
curl "$AGENT_ENV_POOL_URL/api/v1/servers"
# {"meta":{"trace_id":"..."},"data":{"items":[],"total":0}}
```

### 方案二：源码安装

```bash
git clone https://github.com/uvheart/agent-env-pool.git
cd agent-env-pool
pip install -r requirements.txt
python -m agent_env_pool --host 0.0.0.0 --port 8100
```

## 快速开始

### 启动单个容器

```bash
SERVER=$(curl -s -X POST "$AGENT_ENV_POOL_URL/api/v1/servers/boot" \
  -H 'content-type: application/json' \
  -d '{
    "env_type": "browser-use",
    "image": "zenika/alpine-chrome:124",
    "endpoints": [
      {"name": "cdp", "container_port": 9222, "protocol": "cdp", "ready_check": {"type": "cdp"}}
    ],
    "metadata": {
      "command": ["--no-sandbox", "--remote-debugging-address=0.0.0.0", "--remote-debugging-port=9222", "about:blank"],
      "security_opt": ["seccomp=unconfined"]
    }
  }')

echo $SERVER | python3 -m json.tool
```

### 批量启动 — 一次 10 个容器

```bash
ROLLOUT=$(curl -s -X POST "$AGENT_ENV_POOL_URL/api/v1/rollout/boot" \
  -H 'content-type: application/json' \
  -d '{
    "count": 10,
    "env_type": "browser-use",
    "image": "zenika/alpine-chrome:124",
    "endpoints": [{"name": "cdp", "container_port": 9222, "protocol": "cdp", "ready_check": {"type": "cdp"}}],
    "metadata": {
      "command": ["--no-sandbox", "--remote-debugging-address=0.0.0.0", "--remote-debugging-port=9222", "about:blank"],
      "security_opt": ["seccomp=unconfined"]
    }
  }')

ROLLOUT_ID=$(echo $ROLLOUT | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['rollout_id'])")

# 查看 rollout 状态
curl -s "$AGENT_ENV_POOL_URL/api/v1/rollout/$ROLLOUT_ID" | python3 -m json.tool

# 一次性关闭全部 10 个
curl -s -X POST "$AGENT_ENV_POOL_URL/api/v1/rollout/$ROLLOUT_ID/shutdown?force=true"
```

### 使用任意镜像

```bash
# Code-server + HTTP 健康检查
curl -s -X POST "$AGENT_ENV_POOL_URL/api/v1/servers/boot" \
  -H 'content-type: application/json' \
  -d '{
    "env_type": "custom",
    "image": "codercom/code-server:latest",
    "endpoints": [
      {"name": "http", "container_port": 8080, "protocol": "http", "ready_check": {"type": "http", "path": "/"}}
    ]
  }'

# 自定义 ML Worker + TCP 检查
curl -s -X POST "$AGENT_ENV_POOL_URL/api/v1/servers/boot" \
  -H 'content-type: application/json' \
  -d '{
    "env_type": "custom",
    "image": "my-ml-worker:latest",
    "endpoints": [
      {"name": "grpc", "container_port": 50051, "protocol": "tcp", "ready_check": {"type": "tcp"}}
    ]
  }'
```

### 关闭容器

```bash
SERVER_ID=$(echo $SERVER | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['server_id'])")
curl -s -X POST "$AGENT_ENV_POOL_URL/api/v1/servers/$SERVER_ID/shutdown?force=true"
```

## API 参考

### 核心接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/servers/boot` | 启动单个容器 |
| `POST` | `/api/v1/servers/{id}/shutdown` | 关闭容器 |
| `GET` | `/api/v1/servers` | 列出所有活跃容器 |
| `GET` | `/api/v1/servers/{id}` | 获取容器详情 |
| `GET` | `/api/v1/servers/{id}/logs` | 获取容器日志 |

### 池化（acquire/release）

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/pool/acquire` | 获取空闲容器或启动新容器 |
| `POST` | `/api/v1/pool/release/{id}` | 释放回池 |

### 批量 Rollout

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/rollout/boot` | 批量启动 N 个容器 |
| `GET` | `/api/v1/rollout/{rollout_id}` | 查看 rollout 状态 |
| `POST` | `/api/v1/rollout/{rollout_id}/shutdown` | 关闭整个 rollout |

## Python SDK

```python
from agent_env_pool import EnvPoolClient

pool = EnvPoolClient("http://127.0.0.1:8100")

# 单个容器
with pool.acquire(image="zenika/alpine-chrome:124", endpoints=[
    {"name": "cdp", "container_port": 9222, "protocol": "cdp", "ready_check": {"type": "cdp"}}
]) as env:
    print(env.cdp_url)  # 在此连接你的 Agent

# 批量启动
rollout = pool.rollout_boot(count=20, image="my-worker:latest", endpoints=[...])
print(f"启动了 {len(rollout.server_ids)} 个 Worker")
pool.rollout_shutdown(rollout.rollout_id)
```

## 沙箱生命周期

```
starting → running → occupied → stopping → stopped
              ↘          ↘          ↘
                        error
```

配额通过 SQLite `BEGIN IMMEDIATE` 事务在容器创建前原子检查。`starting`、`running`、`occupied`、`stopping` 均计入活跃配额（默认上限：32）。

## 路线图

| 版本 | 目标 |
|---|---|
| V0.1 | 单节点 Docker 沙箱池 ✅ |
| V0.2 | 轨迹收集与 JSONL 导出 |
| V0.3 | 失败回放与浏览器动作适配器 |
| V0.4 | 监控 UI 与运维工具 |
| V0.5 | Kubernetes 与分布式调度 |
