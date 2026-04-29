#!/usr/bin/env bash
set -euo pipefail

"${PYTHON:-python3}" -m pytest tests/test_e2e_browser.py -v -s
"${PYTHON:-python3}" -m pytest tests/test_e2e_parallel_browser.py -v -s
