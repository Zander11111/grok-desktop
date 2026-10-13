#!/bin/sh
# Grok Desktop launcher. Uses the system python3 + the bundled Qt/PySide6 in lib/.
ROOT="@ROOT@"
export PYTHONPATH="$ROOT/lib:$ROOT/app${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-}"
exec python3 -m grok_desktop "$@"
