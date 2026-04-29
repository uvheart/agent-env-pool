#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════════════
#  agent-env-pool 全流程 CI/CD 测试脚本
#
#  完整流程：
#    1. 本地 lint + unit test
#    2. push 到 feature 分支
#    3. GitHub Actions 基础 CI (lint + unit + docker build)
#    4. 测试机 self-hosted runner E2E
#    5. 自动合并到 main
#    6. main 触发 Docker release (build + run + E2E + push image)
#    7. 每步开始/结束飞书通知
#
#  用法:
#    ./scripts/ci/run-full-pipeline.sh                              # 自动 commit + push + 全流程
#    PIPELINE_COMMIT_MSG="feat: xxx" ./scripts/ci/run-full-pipeline.sh  # 自定义 commit message
# ═══════════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

# ── 加载配置 ──────────────────────────────────────────────────────────
SECRETS_FILE="${SECRETS_FILE:-$HOME/Documents/secrets.yaml}"
if [[ -f "$SECRETS_FILE" ]] && command -v python3 &>/dev/null; then
  eval "$(python3 -c "
import yaml, sys
with open('$SECRETS_FILE') as f: s = yaml.safe_load(f)
print(f'FEISHU_WEBHOOK_URL=\"{s[\"feishu\"][\"webhook_url\"]}\"')
print(f'GH_TOKEN=\"{s[\"github\"][\"token\"]}\"')
print(f'REPO=\"{s[\"github\"][\"repo\"]}\"')
" 2>/dev/null || true)"
fi

export FEISHU_WEBHOOK_URL="${FEISHU_WEBHOOK_URL:-}"
export GH_TOKEN="${GH_TOKEN:-}"
REPO="${REPO:-uvheart/agent-env-pool}"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

POLL_INTERVAL=15
PIPELINE_TIMEOUT=600

# ── 工具函数 ──────────────────────────────────────────────────────────

log()  { echo -e "\033[1;36m▶ [$(date '+%H:%M:%S')] $*\033[0m"; }
ok()   { echo -e "\033[1;32m✓ [$(date '+%H:%M:%S')] $*\033[0m"; }
fail() { echo -e "\033[1;31m✗ [$(date '+%H:%M:%S')] $*\033[0m"; }
hr()   { echo -e "\033[90m$(printf '─%.0s' {1..60})\033[0m"; }

notify() {
  local status="$1" stage="$2"
  FEISHU_WEBHOOK_URL="$FEISHU_WEBHOOK_URL" \
  STATUS="$status" WORKFLOW="$stage" \
  REPO="$REPO" REF="$BRANCH" \
  SHA="$(git rev-parse HEAD 2>/dev/null || true)" \
  RUN_ID="${CURRENT_RUN_ID:-local}" \
  RUN_URL="${CURRENT_RUN_URL:-}" \
  ACTOR="$(git config user.name 2>/dev/null || echo local)" \
  JOB="" COMMIT_MSG="$(git log -1 --format=%s 2>/dev/null || true)" \
  bash scripts/ci/notify-feishu.sh "$status" "$stage" 2>/dev/null || true
}

run_step() {
  local step_name="$1"
  shift
  log "$step_name"
  notify start "$step_name"
  if "$@"; then
    ok "$step_name"
    notify success "$step_name"
    return 0
  else
    fail "$step_name"
    notify failure "$step_name"
    return 1
  fi
}

gh_api() {
  curl -sS -H "Authorization: Bearer $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" "$@"
}

wait_workflow() {
  local workflow_name="$1" expected_branch="$2" after_time="$3"
  local deadline=$((SECONDS + PIPELINE_TIMEOUT))

  log "等待 $workflow_name 在 $expected_branch 触发..."

  local run_id=""
  while [[ -z "$run_id" ]]; do
    if (( SECONDS >= deadline )); then
      fail "$workflow_name: 等待触发超时"
      return 1
    fi
    run_id=$(gh_api "https://api.github.com/repos/$REPO/actions/runs?branch=$expected_branch&per_page=5&created=%3E$after_time" \
      | python3 -c "
import sys, json
runs = json.load(sys.stdin).get('workflow_runs', [])
for r in runs:
    if r['name'] == '$workflow_name' and r['status'] != 'completed':
        print(r['id']); break
" 2>/dev/null || true)
    [[ -z "$run_id" ]] && sleep "$POLL_INTERVAL"
  done

  CURRENT_RUN_ID="$run_id"
  CURRENT_RUN_URL="https://github.com/$REPO/actions/runs/$run_id"
  log "$workflow_name 已触发: Run #$run_id"

  while true; do
    if (( SECONDS >= deadline )); then
      fail "$workflow_name Run #$run_id: 执行超时"
      return 1
    fi
    local result
    result=$(gh_api "https://api.github.com/repos/$REPO/actions/runs/$run_id" \
      | python3 -c "
import sys, json; r = json.load(sys.stdin)
print(f\"{r['status']}|{r['conclusion'] or ''}\")" 2>/dev/null)

    local status="${result%%|*}" conclusion="${result##*|}"
    if [[ "$status" == "completed" ]]; then
      if [[ "$conclusion" == "success" ]]; then
        ok "$workflow_name Run #$run_id: $conclusion"
        return 0
      else
        fail "$workflow_name Run #$run_id: $conclusion"
        return 1
      fi
    fi
    echo "   ⏳ $workflow_name: $status ... (${SECONDS}s)"
    sleep "$POLL_INTERVAL"
  done
}

wait_merge() {
  local deadline=$((SECONDS + PIPELINE_TIMEOUT))
  log "等待 PR 自动合并到 main..."

  while true; do
    if (( SECONDS >= deadline )); then
      fail "等待合并超时"
      return 1
    fi
    local main_sha
    main_sha=$(gh_api "https://api.github.com/repos/$REPO/branches/main" \
      | python3 -c "import sys,json;print(json.load(sys.stdin)['commit']['sha'][:7])" 2>/dev/null)
    local pr_state
    pr_state=$(gh_api "https://api.github.com/repos/$REPO/pulls?state=closed&head=${REPO%%/*}:$BRANCH&base=main&per_page=1" \
      | python3 -c "
import sys, json
prs = json.load(sys.stdin)
if prs and prs[0].get('merged_at'): print('merged')
else: print('open')
" 2>/dev/null)

    if [[ "$pr_state" == "merged" ]]; then
      ok "PR 已合并到 main (HEAD: $main_sha)"
      return 0
    fi
    echo "   ⏳ PR 状态: $pr_state ... (${SECONDS}s)"
    sleep "$POLL_INTERVAL"
  done
}

# ═══════════════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════════════

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║          agent-env-pool 全流程 CI/CD 流水线测试              ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
log "分支: $BRANCH | 仓库: $REPO"
hr

# ── Step 1-2: 本地单元测试 ────────────────────────────────────────────
notify start "全流程流水线"

run_step "Step 2: 本地 Lint 检查" python3 -m ruff check .
run_step "Step 2: 本地单元测试" bash -c \
  'AGENT_ENV_POOL_DATABASE_URL="sqlite+aiosqlite:///./agent_env_pool_pipeline.db" python3 -m pytest tests/test_api_smoke.py -q && rm -f agent_env_pool_pipeline.db'
hr

# ── Step 2.5: 自动提交未暂存的改动 ────────────────────────────────────
if [[ -n "$(git status --porcelain)" ]]; then
  log "检测到未提交的改动，自动提交..."
  COMMIT_MSG="${PIPELINE_COMMIT_MSG:-ci: auto-commit from pipeline $(date '+%m%d-%H%M')}"
  git add -A
  git commit -m "$COMMIT_MSG" --no-verify
  ok "已提交: $COMMIT_MSG"
else
  log "工作区干净，无需提交"
fi
hr

# ── Step 3: Push 到 feature 分支 ──────────────────────────────────────
TIMESTAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
run_step "Step 3: Push 到 $BRANCH" git push origin "$BRANCH"
hr

# ── Step 4: 等待 GitHub CI ────────────────────────────────────────────
notify start "Step 4: GitHub CI"
if wait_workflow "CI" "$BRANCH" "$TIMESTAMP"; then
  notify success "Step 4: GitHub CI"
else
  notify failure "Step 4: GitHub CI"
  fail "CI 失败，流程中止"; notify failure "全流程流水线"; exit 1
fi
hr

# ── Step 4.5: 创建 PR (触发 E2E + Auto Merge) ────────────────────────
log "创建 / 更新 PR ..."
EXISTING_PR=$(gh_api "https://api.github.com/repos/$REPO/pulls?head=${REPO%%/*}:$BRANCH&base=main&state=open" \
  | python3 -c "import sys,json;prs=json.load(sys.stdin);print(prs[0]['number'] if prs else '')" 2>/dev/null || true)

if [[ -n "$EXISTING_PR" ]]; then
  log "PR #$EXISTING_PR 已存在，复用"
  PR_NUMBER="$EXISTING_PR"
else
  PR_BODY="自动化全流程测试 - $(date '+%Y-%m-%d %H:%M')"
  PR_RESULT=$(gh_api -X POST "https://api.github.com/repos/$REPO/pulls" \
    -d "{\"title\":\"ci: pipeline test $(date '+%m%d-%H%M')\",\"head\":\"$BRANCH\",\"base\":\"main\",\"body\":\"$PR_BODY\"}")
  PR_NUMBER=$(echo "$PR_RESULT" | python3 -c "import sys,json;print(json.load(sys.stdin).get('number',''))" 2>/dev/null)
  ok "创建 PR #$PR_NUMBER"
fi
hr

# ── Step 5: 等待 E2E ──────────────────────────────────────────────────
notify start "Step 5: E2E 测试"
if wait_workflow "E2E" "$BRANCH" "$TIMESTAMP"; then
  notify success "Step 5: E2E 测试"
else
  notify failure "Step 5: E2E 测试"
  fail "E2E 失败，流程中止"; notify failure "全流程流水线"; exit 1
fi
hr

# ── Step 6: 等待自动合并 ──────────────────────────────────────────────
notify start "Step 6: 自动合并"
if wait_merge; then
  notify success "Step 6: 自动合并"
else
  notify failure "Step 6: 自动合并"
  fail "合并失败，流程中止"; notify failure "全流程流水线"; exit 1
fi

git fetch origin main 2>/dev/null
hr

# ── Step 7: 等待 Docker Release ───────────────────────────────────────
MERGE_TIMESTAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
notify start "Step 7: Docker Release"
if wait_workflow "Release" "main" "$MERGE_TIMESTAMP"; then
  notify success "Step 7: Docker Release"
else
  notify failure "Step 7: Docker Release"
  fail "Release 失败"; notify failure "全流程流水线"; exit 1
fi
hr

# ── 完成 ──────────────────────────────────────────────────────────────
echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                    ✅ 全流程测试通过                         ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
notify success "全流程流水线"
