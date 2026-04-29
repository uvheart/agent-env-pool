#!/usr/bin/env bash
set -euo pipefail

"${PYTHON:-python3}" -m ruff check .
"${PYTHON:-python3}" -m pytest tests/test_api_smoke.py -q
docker build -t agent-env-pool:local .
