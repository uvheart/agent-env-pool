#!/usr/bin/env bash
set -euo pipefail

python -m pytest tests/test_e2e_browser.py -v -s
