"""Voice model catalog + first-use download. Models are NOT bundled in the .deb: they're fetched once from the
sherpa-onnx GitHub releases (k2-fsa) into <data dir>/voice/models, with progress. Override the folder with GD_VOICE_MODELS."""
from __future__ import annotations

import os
import shutil
import tarfile
import urllib.request

from .. import paths

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"

# key -> info.  `keep` = archive members (relative to the top folder) to extract; None = everything.
STT = {
    "zipformer-en-20M": {
        "label": "English (streaming Zipformer 20M, int8)",
        "url": "asr-models/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17.tar.bz2", "download_mb": 122, "disk_mb": 44,
        "keep": ["tokens.txt", "encoder-epoch-99-avg-1.int8.onnx", "decoder-epoch-99-avg-1.int8.onnx",
                 "joiner-epoch-99-avg-1.int8.onnx"],
        "check": "encoder-epoch-99-avg-1.int8.onnx",
    },
}
TTS = {
    "lessac-medium": {"label": "Lessac (US, female-ish, clear)", "url": "tts-models/vits-piper-en_US-lessac-medium.tar.bz2",
                      "download_mb": 64, "disk_mb": 82, "model": "en_US-lessac-medium.onnx"},
    "amy-low": {"label": "Amy (US, light)", "url": "tts-models/vits-piper-en_US-amy-low.tar.bz2",
                "download_mb": 64, "disk_mb": 82, "model": "en_US-amy-low.onnx"},
    "ryan-low": {"label": "Ryan (US, male)", "url": "tts-models/vits-piper-en_US-ryan-low.tar.bz2",
                 "download_mb": 64, "disk_mb": 82, "model": "en_US-ryan-low.onnx"},
}
DEFAULT_STT, DEFAULT_TTS = "zipformer-en-20M", "lessac-medium"


def models_dir() -> str:
    d = os.environ.get("GD_VOICE_MODELS") or os.path.join(paths.data_dir(), "voice", "models")
    os.makedirs(d, exist_ok=True)
    return d


def stt_dir(key: str = DEFAULT_STT) -> str:
    return os.path.join(models_dir(), "stt-" + key)


def tts_dir(key: str = DEFAULT_TTS) -> str:
    return os.path.join(models_dir(), "tts-" + key)


def stt_ready(key: str = DEFAULT_STT) -> bool:
    return os.path.isfile(os.path.join(stt_dir(key), STT[key]["check"]))


def tts_ready(key: str = DEFAULT_TTS) -> bool:
    return key in TTS and os.path.isfile(os.path.join(tts_dir(key), TTS[key]["model"]))


def installed_voices() -> list[str]:
    return [k for k in TTS if tts_ready(k)]


class Cancelled(Exception):
    pass


def _download(url: str, dest: str, keep, progress, cancelled) -> None:
    """Stream the .tar.bz2 and extract only the wanted files into dest (a temp dir that is renamed when complete)."""
    tmp = dest + ".part"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    req = urllib.request.Request(url, headers={"User-Agent": "grok-desktop"})
    with urllib.request.urlopen(req, timeout=30) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = [0]

        class Counting:
            def read(self, n=-1):
                if cancelled():
                    raise Cancelled()
                b = r.read(n)
                done[0] += len(b)
                progress(done[0], total)
                return b

        with tarfile.open(fileobj=Counting(), mode="r|bz2") as t:
            for m in t:
                if not m.isfile():
                    continue
                rel = m.name.split("/", 1)[1] if "/" in m.name else m.name
                if keep is not None and rel not in keep:
                    continue
                out = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with t.extractfile(m) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)


def ensure(kind: str, key: str, progress=lambda done, total: None, cancelled=lambda: False) -> str:
    """Make sure a model is on disk (downloads if needed). kind = 'stt' | 'tts'. Returns its folder. May raise OSError/Cancelled."""
    if kind == "stt":
        info, dest, ready = STT[key], stt_dir(key), stt_ready(key)
    else:
        info, dest, ready = TTS[key], tts_dir(key), tts_ready(key)
    if not ready:
        _download(BASE + info["url"], dest, info.get("keep"), progress, cancelled)
    return dest
