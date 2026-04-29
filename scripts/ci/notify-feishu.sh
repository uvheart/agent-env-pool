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

REPO="${GITHUB_REPOSITORY:-${REPO:-uvheart/agent-env-pool}}"
REF="${GITHUB_REF_NAME:-${REF:-main}}"
SHA="${GITHUB_SHA:-${SHA:-}}"
SHORT_SHA="${SHA:0:7}"
RUN_ID="${GITHUB_RUN_ID:-${RUN_ID:-}}"
RUN_URL="${RUN_URL:-${GITHUB_SERVER_URL:-https://github.com}/${REPO}/actions/runs/${RUN_ID}}"
ACTOR="${GITHUB_ACTOR:-${ACTOR:-$(git config user.name 2>/dev/null || echo unknown)}}"
JOB="${GITHUB_JOB:-${JOB:-}}"
START_TIME="${START_TIME:-$(date -u '+%Y-%m-%d %H:%M:%S UTC')}"

# Fetch commit message
COMMIT_MSG=""
if [[ -n "${SHA}" ]] && [[ -n "${GITHUB_TOKEN:-}" ]]; then
  COMMIT_MSG=$(curl -s -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    "${GITHUB_API_URL:-https://api.github.com}/repos/${REPO}/commits/${SHA}" \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("commit",{}).get("message","").split(chr(10))[0][:80])' 2>/dev/null || true)
fi

# Build and send interactive card
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

STATUS_CFG = {
    "start":   {"color": "blue",   "icon": "\U0001f680", "label": "\u5f00\u59cb\u8fd0\u884c"},
    "success": {"color": "green",  "icon": "\u2705",     "label": "\u6267\u884c\u6210\u529f"},
    "failure": {"color": "red",    "icon": "\u274c",     "label": "\u6267\u884c\u5931\u8d25"},
}
cfg = STATUS_CFG.get(status, STATUS_CFG["failure"])

elements = []

# --- row 1: trigger info (two columns) ---
col_left = []
if actor:
    col_left.append({"tag": "div", "text": {"tag": "lark_md", "content": f"\U0001f464 **\u89e6\u53d1\u8005**\n{actor}"}})
col_left.append({"tag": "div", "text": {"tag": "lark_md", "content": f"\U0001f4c5 **\u65f6\u95f4**\n{start_time}"}})

col_right = []
col_right.append({"tag": "div", "text": {"tag": "lark_md", "content": f"\U0001f33f **\u5206\u652f**\n`{ref}`"}})
if job:
    col_right.append({"tag": "div", "text": {"tag": "lark_md", "content": f"\u2699\ufe0f **Job**\n`{job}`"}})

elements.append({
    "tag": "column_set",
    "flex_mode": "bisect",
    "background_style": "grey",
    "columns": [
        {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "top", "elements": col_left},
        {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "top", "elements": col_right},
    ]
})

# --- row 2: commit info ---
if short_sha:
    commit_text = f"\U0001f4e6 **\u63d0\u4ea4:** [`{short_sha}`](https://github.com/{repo}/commit/{sha})"
    if commit_msg:
        commit_text += f"  \u2014  {commit_msg}"
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": commit_text}})

# --- extra fields ---
if extra:
    try:
        extra_lines = []
        for k, v in json.loads(extra).items():
            extra_lines.append(f"**{k}:** {v}")
        if extra_lines:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(extra_lines)}})
    except Exception:
        pass

# --- divider ---
elements.append({"tag": "hr"})

# --- action button ---
elements.append({
    "tag": "action",
    "actions": [{
        "tag": "button",
        "text": {"tag": "plain_text", "content": "\U0001f50d \u67e5\u770b\u8be6\u60c5"},
        "type": "primary" if status == "success" else ("default" if status == "start" else "danger"),
        "url": run_url,
    }]
})

# --- footer note ---
elements.append({
    "tag": "note",
    "elements": [
        {"tag": "plain_text", "content": f"{repo}  \u00b7  Run #{run_id}"},
    ]
})

card = {
    "msg_type": "interactive",
    "card": {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": cfg["color"],
            "title": {"tag": "plain_text", "content": "{} {} {}".format(cfg["icon"], workflow, cfg["label"])},
        },
        "elements": elements,
    }
}
print(json.dumps(card, ensure_ascii=False))
' | curl -fsS -X POST "$FEISHU_WEBHOOK_URL" -H "Content-Type: application/json" -d @/dev/stdin >/dev/null

echo "Feishu notification sent: $STATUS $WORKFLOW"
