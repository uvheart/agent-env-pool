#!/usr/bin/env bash
set -euo pipefail

"${PYTHON:-python3}" -m pytest tests/test_e2e_browser.py -v -s
