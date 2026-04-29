#!/usr/bin/env bash
set -euo pipefail

# Usage: notify-feishu.sh <status> <workflow_name> [extra_context_json]
# status: start | success | failure

if [[ -z "${FEISHU_WEBHOOK_URL:-}" ]]; then
  echo "FEISHU_WEBHOOK_URL is not set; skip notification"
  exit 0
fi

STATUS="${1:-failure}"
WORKFLOW="${2:-agent-env-pool}"
EXTRA="${3:-}"

REPO="${GITHUB_REPOSITORY:-uvheart/agent-env-pool}"
REF="${GITHUB_REF_NAME:-main}"
SHA="${GITHUB_SHA:-}"
SHORT_SHA="${SHA:0:7}"
RUN_ID="${GITHUB_RUN_ID:-}"
RUN_URL="${GITHUB_SERVER_URL:-https://github.com}/${REPO}/actions/runs/${RUN_ID}"
ACTOR="${GITHUB_ACTOR:-}"
JOB="${GITHUB_JOB:-}"
START_TIME="$(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# Fetch commit message
COMMIT_MSG=""
if [[ -n "${SHA}" ]] && [[ -n "${GITHUB_TOKEN:-}" ]]; then
  COMMIT_MSG=$(curl -s -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    "${GITHUB_API_URL:-https://api.github.com}/repos/${REPO}/commits/${SHA}" \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("commit",{}).get("message","").split(chr(10))[0][:80])' 2>/dev/null || true)
fi

# Build and send card
python3 -c '
import json, os

status = os.environ.get("STATUS", "failure")
workflow = os.environ.get("WORKFLOW", "agent-env-pool")
repo = os.environ.get("REPO", "uvheart/agent-env-pool")
ref = os.environ.get("REF", "main")
sha = os.environ.get("SHA", "")
short_sha = sha[:7] if sha else ""
run_id = os.environ.get("RUN_ID", "")
run_url = os.environ.get("RUN_URL", "")
actor = os.environ.get("ACTOR", "")
start_time = os.environ.get("START_TIME", "")
commit_msg = os.environ.get("COMMIT_MSG", "")
job = os.environ.get("JOB", "")
extra = os.environ.get("EXTRA", "")

if status == "start": color, icon, title = "blue", "🚀", workflow + " 开始运行"
elif status == "success": color, icon, title = "green", "✅", workflow + " 执行成功"
else: color, icon, title = "red", "❌", workflow + " 执行失败"

lines = [f"**开始时间:** {start_time}", f"**触发者:** {actor}", f"**分支:** {ref}"]

if short_sha:
    commit_line = f"**提交:** [{short_sha}](https://github.com/{repo}/commit/{sha})"
    if commit_msg: commit_line += f" - {commit_msg}"
    lines.append(commit_line)

if job: lines.append(f"**Job:** {job}")
lines.append(f"**详情:** [Run #{run_id}]({run_url})")

if extra:
    try:
        for k, v in json.loads(extra).items(): lines.append(f"**{k}:** {v}")
    except: pass

card = {
    "msg_type": "interactive",
    "card": {
        "config": {"wide_screen_mode": True},
        "header": {"template": color, "title": {"tag": "plain_text", "content": icon + " " + title}},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": chr(10).join(lines)}}]
    }
}
print(json.dumps(card, ensure_ascii=False))
' | curl -fsS -X POST "$FEISHU_WEBHOOK_URL" -H "Content-Type: application/json" -d @/dev/stdin >/dev/null

echo "Feishu notification sent: $STATUS $WORKFLOW"
