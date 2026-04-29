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
HEAD_SHA="$(git rev-parse HEAD)"
PR_NUMBER=""
VERSION_FILE="VERSION"
CHANGELOG_FILE="CHANGELOG.md"
RELEASE_VERSION=""
RELEASE_NOTES=""

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
  EVENT="local-pipeline" \
  TRIGGER_SOURCE="开发机器（wl） / local shell / host: $(hostname 2>/dev/null || echo unknown)" \
  HOST_NAME="$(hostname 2>/dev/null || echo unknown)" \
  ACTOR="$(git config user.name 2>/dev/null || echo local)" \
  JOB="" COMMIT_MSG="$(git log -1 --format=%s 2>/dev/null || true)" \
  bash scripts/ci/notify-feishu.sh "$status" "$stage" 2>/dev/null || true
}

next_patch_version() {
  python3 - <<'PY'
from pathlib import Path

path = Path("VERSION")
raw = path.read_text().strip() if path.exists() else "0.0.0"
try:
    major, minor, patch = [int(part) for part in raw.split(".")]
except Exception:
    major, minor, patch = 0, 0, 0

if raw == "0.0.0" and not path.exists():
    print("0.1.0")
else:
    print(f"{major}.{minor}.{patch + 1}")
PY
}

write_version_notes() {
  local version="$1" notes="$2"
  local date_utc
  date_utc="$(date -u '+%Y-%m-%d %H:%M:%S UTC')"

  echo "$version" > "$VERSION_FILE"

  local old_changelog=""
  if [[ -f "$CHANGELOG_FILE" ]]; then
    old_changelog="$(cat "$CHANGELOG_FILE")"
  fi

  {
    echo "# Changelog"
    echo ""
    echo "## v$version - $date_utc"
    echo ""
    echo "$notes" | sed 's/^/- /'
    echo ""
    if [[ -n "$old_changelog" ]]; then
      echo "$old_changelog" | sed '1{/^# Changelog$/d;}' | sed '1{/^$/d;}'
    fi
  } > "$CHANGELOG_FILE"
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

print_run_jobs() {
  local run_id="$1"
  gh_api "https://api.github.com/repos/$REPO/actions/runs/$run_id/jobs" \
    | python3 -c '
import json, sys
jobs = json.load(sys.stdin).get("jobs", [])
for job in jobs:
    name = job["name"]
    status = job["status"]
    conclusion = job.get("conclusion") or "-"
    print(f"   Job: {name} | {status} | {conclusion}")
    for step in job.get("steps", []):
        mark = "✓" if step.get("conclusion") == "success" else ("✗" if step.get("conclusion") == "failure" else "○")
        step_name = step["name"]
        step_state = step.get("conclusion") or step.get("status")
        print(f"      {mark} {step_name} ({step_state})")
'
}

print_run_failure_logs() {
  local run_id="$1"
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  log "下载失败日志: https://github.com/$REPO/actions/runs/$run_id"
  if gh_api -L "https://api.github.com/repos/$REPO/actions/runs/$run_id/logs" -o "$tmp_dir/logs.zip" >/dev/null 2>&1; then
    unzip -q "$tmp_dir/logs.zip" -d "$tmp_dir/logs" >/dev/null 2>&1 || true
    echo "   ── 失败日志摘要 ──"
    grep -RniE "error|failed|failure|traceback|exception|assertion|importerror" "$tmp_dir/logs" 2>/dev/null \
      | head -80 \
      | sed 's/^/   /' || true
    echo "   ── 摘要结束 ──"
  else
    fail "日志下载失败，请手动打开 run URL 查看"
  fi
  rm -rf "$tmp_dir"
}

wait_workflow() {
  local workflow_name="$1" expected_branch="$2" expected_sha="$3"
  local deadline=$((SECONDS + PIPELINE_TIMEOUT))

  log "等待 $workflow_name 触发..."
  echo "   branch: $expected_branch"
  echo "   commit: ${expected_sha:0:7}"

  local run_id=""
  while [[ -z "$run_id" ]]; do
    if (( SECONDS >= deadline )); then
      fail "$workflow_name: 等待触发超时"
      return 1
    fi
    run_id=$(gh_api "https://api.github.com/repos/$REPO/actions/runs?branch=$expected_branch&per_page=20" \
      | python3 -c "
import sys, json
runs = json.load(sys.stdin).get('workflow_runs', [])
for r in runs:
    if r['name'] == '$workflow_name' and r.get('head_sha') == '$expected_sha':
        print(r['id']); break
" 2>/dev/null || true)
    [[ -z "$run_id" ]] && sleep "$POLL_INTERVAL"
  done

  CURRENT_RUN_ID="$run_id"
  CURRENT_RUN_URL="https://github.com/$REPO/actions/runs/$run_id"
  log "$workflow_name 已触发"
  echo "   run_id: $run_id"
  echo "   url:    $CURRENT_RUN_URL"

  while true; do
    if (( SECONDS >= deadline )); then
      fail "$workflow_name Run #$run_id: 执行超时"
      print_run_jobs "$run_id"
      return 1
    fi
    local result
    result=$(gh_api "https://api.github.com/repos/$REPO/actions/runs/$run_id" \
      | python3 -c "
import sys, json; r = json.load(sys.stdin)
print(f\"{r['status']}|{r['conclusion'] or ''}|{r.get('run_started_at') or ''}\")" 2>/dev/null)

    local status conclusion started_at
    IFS="|" read -r status conclusion started_at <<< "$result"
    if [[ "$status" == "completed" ]]; then
      if [[ "$conclusion" == "success" ]]; then
        ok "$workflow_name Run #$run_id: $conclusion"
        print_run_jobs "$run_id"
        return 0
      else
        fail "$workflow_name Run #$run_id: $conclusion"
        print_run_jobs "$run_id"
        print_run_failure_logs "$run_id"
        return 1
      fi
    fi
    echo "   ⏳ $workflow_name: $status | started_at=${started_at:-unknown} | elapsed=${SECONDS}s"
    sleep "$POLL_INTERVAL"
  done
}

wait_merge() {
  local deadline=$((SECONDS + PIPELINE_TIMEOUT))
  log "等待 PR #$PR_NUMBER 自动合并到 main..."

  while true; do
    if (( SECONDS >= deadline )); then
      fail "等待合并超时"
      return 1
    fi
    local pr_info
    pr_info=$(gh_api "https://api.github.com/repos/$REPO/pulls/$PR_NUMBER" \
      | python3 -c "
import sys, json
pr = json.load(sys.stdin)
print('|'.join([
    str(pr.get('state') or ''),
    str(pr.get('mergeable_state') or ''),
    str(pr.get('merged_at') or ''),
    str(pr.get('merge_commit_sha') or ''),
]))
" 2>/dev/null)

    local state mergeable_state merged_at merge_commit_sha
    IFS="|" read -r state mergeable_state merged_at merge_commit_sha <<< "$pr_info"
    if [[ -n "$merged_at" && "$merged_at" != "None" ]]; then
      ok "PR #$PR_NUMBER 已合并到 main"
      echo "   merged_at: $merged_at"
      echo "   merge_commit: ${merge_commit_sha:0:7}"
      HEAD_SHA="$merge_commit_sha"
      return 0
    fi
    echo "   ⏳ PR #$PR_NUMBER: state=$state mergeable_state=$mergeable_state elapsed=${SECONDS}s"
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
  RELEASE_VERSION="$(next_patch_version)"
  RELEASE_NOTES="${PIPELINE_RELEASE_NOTES:-${PIPELINE_COMMIT_MSG:-自动流水线提交 $(date '+%Y-%m-%d %H:%M')}}"
  COMMIT_MSG="${PIPELINE_COMMIT_MSG:-chore(release): v$RELEASE_VERSION}"
  write_version_notes "$RELEASE_VERSION" "$RELEASE_NOTES"
  log "生成版本说明"
  echo "   version: v$RELEASE_VERSION"
  echo "   notes:   $RELEASE_NOTES"
  git add -A
  git commit -m "v$RELEASE_VERSION: $COMMIT_MSG" --no-verify
  HEAD_SHA="$(git rev-parse HEAD)"
  ok "已提交: v$RELEASE_VERSION: $COMMIT_MSG"
  echo "   commit: ${HEAD_SHA:0:7}"
else
  log "工作区干净，无需提交"
  HEAD_SHA="$(git rev-parse HEAD)"
  RELEASE_VERSION="$(cat "$VERSION_FILE" 2>/dev/null || echo unknown)"
  RELEASE_NOTES="$(git log -1 --format=%s 2>/dev/null || true)"
  echo "   commit: ${HEAD_SHA:0:7}"
  echo "   version: v$RELEASE_VERSION"
fi
hr

# ── Step 2.6: 同步 main，避免 PR dirty 导致 E2E / auto-merge 不触发 ─────
run_step "Step 2.6: 同步 origin/main" git fetch origin main
if ! git merge --no-edit origin/main; then
  fail "同步 origin/main 失败：存在合并冲突，请先人工解决冲突后重跑脚本"
  git status --short
  notify failure "Step 2.6: 同步 origin/main"
  notify failure "全流程流水线"
  exit 1
fi
HEAD_SHA="$(git rev-parse HEAD)"
ok "已同步 origin/main"
echo "   commit: ${HEAD_SHA:0:7}"
hr

# ── Step 3: Push 到 feature 分支 ──────────────────────────────────────
run_step "Step 3: Push 到 $BRANCH" git push origin "$BRANCH"
hr

# ── Step 4: 等待 GitHub CI ────────────────────────────────────────────
notify start "Step 4: GitHub CI"
if wait_workflow "CI" "$BRANCH" "$HEAD_SHA"; then
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
  PR_PAYLOAD="$(mktemp)"
  RELEASE_VERSION="$RELEASE_VERSION" COMMIT_MSG="$COMMIT_MSG" RELEASE_NOTES="$RELEASE_NOTES" \
    python3 - <<'PY' > "$PR_PAYLOAD"
import json
import os

version = os.environ["RELEASE_VERSION"]
commit_msg = os.environ["COMMIT_MSG"]
release_notes = os.environ["RELEASE_NOTES"]

print(json.dumps({
    "title": f"v{version}: {commit_msg}",
    "body": f"版本: v{version}\n\n版本说明:\n{release_notes}\n\n自动化全流程测试更新。",
}, ensure_ascii=False))
PY
  gh_api -X PATCH "https://api.github.com/repos/$REPO/pulls/$PR_NUMBER" -d @"$PR_PAYLOAD" >/dev/null
  rm -f "$PR_PAYLOAD"
  ok "已更新 PR #$PR_NUMBER 的版本说明"
else
  PR_BODY="自动化全流程测试 - $(date '+%Y-%m-%d %H:%M')"
  PR_PAYLOAD="$(mktemp)"
  RELEASE_VERSION="$RELEASE_VERSION" COMMIT_MSG="$COMMIT_MSG" BRANCH="$BRANCH" RELEASE_NOTES="$RELEASE_NOTES" PR_BODY="$PR_BODY" \
    python3 - <<'PY' > "$PR_PAYLOAD"
import json
import os

version = os.environ["RELEASE_VERSION"]
commit_msg = os.environ["COMMIT_MSG"]
branch = os.environ["BRANCH"]
release_notes = os.environ["RELEASE_NOTES"]
pr_body = os.environ["PR_BODY"]

print(json.dumps({
    "title": f"v{version}: {commit_msg}",
    "head": branch,
    "base": "main",
    "body": f"版本: v{version}\n\n版本说明:\n{release_notes}\n\n{pr_body}",
}, ensure_ascii=False))
PY
  PR_RESULT=$(gh_api -X POST "https://api.github.com/repos/$REPO/pulls" -d @"$PR_PAYLOAD")
  rm -f "$PR_PAYLOAD"
  PR_NUMBER=$(echo "$PR_RESULT" | python3 -c "import sys,json;print(json.load(sys.stdin).get('number',''))" 2>/dev/null)
  ok "创建 PR #$PR_NUMBER"
fi
echo "   url: https://github.com/$REPO/pull/$PR_NUMBER"
hr

# ── Step 5: 等待 E2E ──────────────────────────────────────────────────
notify start "Step 5: E2E 测试"
if wait_workflow "E2E" "$BRANCH" "$HEAD_SHA"; then
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
notify start "Step 7: Docker Release"
if wait_workflow "Release" "main" "$HEAD_SHA"; then
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
