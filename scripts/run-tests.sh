#!/usr/bin/env bash
# All tests run against tests/fake_grok.py (no real grok, no network, no real mouse/keyboard).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || { python3 -m venv .venv && .venv/bin/pip install -q PySide6-Essentials pytest markdown pygments; }
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -p no:cacheprovider tests "$@"
