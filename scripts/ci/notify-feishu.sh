#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${FEISHU_WEBHOOK_URL:-}" ]]; then
  echo "FEISHU_WEBHOOK_URL is not set; skip notification"
  exit 0
fi

STATUS="${1:-failure}"
TITLE="${2:-agent-env-pool pipeline notification}"
RUN_URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-uvheart/agent-env-pool}/actions/runs/${GITHUB_RUN_ID:-}"
export STATUS TITLE RUN_URL

payload="$(
  python - <<'PY'
import json
import os

status = os.environ.get("STATUS", "failure")
title = os.environ.get("TITLE", "agent-env-pool pipeline notification")
text = "\n".join(
    [
        title,
        f"status: {status}",
        f"repository: {os.environ.get('GITHUB_REPOSITORY', '')}",
        f"workflow: {os.environ.get('GITHUB_WORKFLOW', '')}",
        f"job: {os.environ.get('GITHUB_JOB', '')}",
        f"ref: {os.environ.get('GITHUB_REF_NAME', '')}",
        f"sha: {os.environ.get('GITHUB_SHA', '')}",
        f"run: {os.environ.get('RUN_URL', '')}",
    ]
)
print(json.dumps({"msg_type": "text", "content": {"text": text}}, ensure_ascii=False))
PY
)"

curl -fsS -X POST "$FEISHU_WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d "$payload" >/dev/null
