"""sherpa-onnx wrappers: streaming speech recognizer (with endpointing as the VAD) and Piper (VITS) text-to-speech.
No Qt. Importing sherpa_onnx: the bundled copy in <app>/lib is on PYTHONPATH when installed from the .deb/tarball."""
from __future__ import annotations

import os
import re
import threading

from . import audio, models


def sherpa():
    try:
        import sherpa_onnx
        return sherpa_onnx
    except ImportError as e:
        raise RuntimeError("The voice engine (sherpa-onnx) isn't installed for this Python. "
                           "Run:  pip install --user sherpa-onnx   (the Linux .deb includes it)") from e


def tidy(text: str) -> str:
    """The recognizer returns ALL CAPS with no punctuation: make it a normal sentence."""
    t = text.strip().lower()
    if not t:
        return ""
    t = re.sub(r"\bi\b", "I", t)
    t = re.sub(r"\bi(['’]m|['’]ll|['’]d|['’]ve)\b", lambda m: "I" + m.group(1), t)
    t = t[0].upper() + t[1:]
    if t[-1] not in ".?!":
        q = re.match(r"(what|why|how|when|where|who|which|is|are|can|could|would|do|does|did|will|should)\b", t.lower())
        t += "?" if q else "."
    return t


class Stt:
    def __init__(self, key: str = models.DEFAULT_STT, threads: int = 2):
        s = sherpa()
        d = models.stt_dir(key)
        self.rec = s.OnlineRecognizer.from_transducer(
            tokens=os.path.join(d, "tokens.txt"),
            encoder=os.path.join(d, "encoder-epoch-99-avg-1.int8.onnx"),
            decoder=os.path.join(d, "decoder-epoch-99-avg-1.int8.onnx"),
            joiner=os.path.join(d, "joiner-epoch-99-avg-1.int8.onnx"),
            num_threads=threads, sample_rate=audio.RATE, decoding_method="greedy_search",
            enable_endpoint_detection=True, rule1_min_trailing_silence=2.4, rule2_min_trailing_silence=0.9,
            rule3_min_utterance_length=25)

    def stream(self) -> "Utterance":
        return Utterance(self.rec)


class Utterance:
    """One spoken sentence/phrase. feed() audio; endpointed() says the speaker stopped (VAD); text() is the transcript so far."""

    def __init__(self, rec):
        self.rec = rec
        self.st = rec.create_stream()
        self.samples = 0

    def feed(self, pcm16: bytes) -> None:
        self.st.accept_waveform(audio.RATE, audio.pcm16_to_floats(pcm16))
        self.samples += len(pcm16) // 2
        while self.rec.is_ready(self.st):
            self.rec.decode_stream(self.st)

    def endpointed(self) -> bool:
        return self.rec.is_endpoint(self.st)

    def text(self) -> str:
        r = self.rec.get_result(self.st)
        return (r if isinstance(r, str) else getattr(r, "text", "")).strip()

    def finish(self) -> str:
        self.st.accept_waveform(audio.RATE, [0.0] * int(audio.RATE * 0.4))   # tail padding so the last word is decoded
        self.st.input_finished()
        while self.rec.is_ready(self.st):
            self.rec.decode_stream(self.st)
        return tidy(self.text())

    def seconds(self) -> float:
        return self.samples / audio.RATE


class Tts:
    def __init__(self, voice: str = models.DEFAULT_TTS, threads: int = 2):
        s = sherpa()
        d = models.tts_dir(voice)
        cfg = s.OfflineTtsConfig(model=s.OfflineTtsModelConfig(
            vits=s.OfflineTtsVitsModelConfig(model=os.path.join(d, models.TTS[voice]["model"]),
                                             tokens=os.path.join(d, "tokens.txt"),
                                             data_dir=os.path.join(d, "espeak-ng-data")),
            num_threads=threads))
        self.tts = s.OfflineTts(cfg)
        self.voice = voice
        self.lock = threading.Lock()

    def synth(self, text: str, speed: float = 1.0):
        with self.lock:
            a = self.tts.generate(text, sid=0, speed=speed)
        return list(a.samples), a.sample_rate
