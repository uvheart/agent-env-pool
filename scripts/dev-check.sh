#!/usr/bin/env bash
set -euo pipefail

python -m ruff check .
python -m pytest tests/test_api_smoke.py -q
docker build -t agent-env-pool:local .
