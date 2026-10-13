#!/usr/bin/env bash
# Download the voice models into .voice-models/ (used by tests/test_voice.py; the app itself downloads on first use).
set -euo pipefail
cd "$(dirname "$0")/.."
GD_VOICE_MODELS="$PWD/.voice-models" PYTHONPATH=src .venv/bin/python - <<'PY'
from grok_desktop.voice import models
for kind, key in (("stt", models.DEFAULT_STT), ("tts", models.DEFAULT_TTS)):
    last = [-1]
    def prog(d, t):
        p = int(d * 100 / t) if t else 0
        if p // 10 != last[0] // 10: last[0] = p; print(kind, key, f"{p}%", flush=True)
    print(models.ensure(kind, key, prog))
PY
du -sh .voice-models/*
