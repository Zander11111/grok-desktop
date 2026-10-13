"""Voice mode: local/offline speech-to-text and text-to-speech (sherpa-onnx; no cloud, no API key).

Design: everything below `manager.py` is plain Python (no Qt) so the same voice module can be reused by another app
(e.g. Grid OS Companion). A wake word is not part of this module yet; `Listener` takes an optional `gate` hook
so a keyword spotter can be put in front of it later.
"""
