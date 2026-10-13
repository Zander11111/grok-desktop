"""VoiceManager: ties microphone -> local STT -> chat (Controller.send) and grok's streamed reply -> local TTS -> speakers.
States: idle | loading | listening | thinking | speaking | muted | error."""
from __future__ import annotations

import queue
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from . import audio, engine, models
from .textfilter import SpeechFilter

SPEAK_MODES = ("voice", "always", "off")      # only after voice input | always | off
SPEAK_LABELS = {"voice": "Only after I used my voice", "always": "Always", "off": "Never (text only)"}


class VoiceManager(QObject):
    stateChanged = Signal(str)
    levelChanged = Signal(float)         # 0..1 mic level (listening)
    partial = Signal(str)                # live transcript while talking
    notice = Signal(str)
    progress = Signal(str, int)          # what, percent (-1 = done)
    mutedChanged = Signal(bool)
    handsFreeChanged = Signal(bool)
    transcribed = Signal(str)            # a finished utterance (before it's sent)
    _ttsActivity = Signal(bool)          # worker threads -> main: still speaking?
    _utterance = Signal(str)
    _err = Signal(str)
    _ready = Signal(str)

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.settings = ctl.settings
        self.state = "idle"
        self.muted = False
        self.hands_free = False
        self.stt: engine.Stt | None = None
        self.tts: engine.Tts | None = None
        self._loading = False
        self._download_cancel = False
        self._rec: audio.Recorder | None = None
        self._utt: engine.Utterance | None = None
        self._lock = threading.Lock()
        self._ptt = False
        self._held = False
        self._accept_audio = False
        self._last_partial = ""
        self._filter = SpeechFilter()
        self._player = audio.Player()
        self._synth_q: queue.Queue = queue.Queue()
        self._play_q: queue.Queue = queue.Queue()
        self._epoch = 0
        self._pending = 0
        self._plock = threading.Lock()
        self._turn_open = False
        self._speaking_turn = False
        self._pending_text = ""
        self._threads_started = False
        self._after_load = None
        self._level_t = 0.0
        self._utterance.connect(self._on_utterance)
        self._err.connect(self._on_error)
        self._ttsActivity.connect(self._on_tts_activity)
        self._ready.connect(self._on_ready)
        c = ctl
        c.assistantText.connect(self._on_assistant_text)
        c.toolStep.connect(self._on_tool_step)
        c.turnEnded.connect(self._on_turn_ended)

    # ------------------------------------------------------------------ settings --
    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("voice_enabled", True))

    def speak_mode(self) -> str:
        m = self.settings.get("voice_speak", "voice")
        return m if m in SPEAK_MODES else "voice"

    def voice_key(self) -> str:
        k = self.settings.get("voice_voice", models.DEFAULT_TTS)
        return k if k in models.TTS else models.DEFAULT_TTS

    def speed(self) -> float:
        try:
            return max(0.5, min(2.0, float(self.settings.get("voice_speed", 1.0))))
        except (TypeError, ValueError):
            return 1.0

    # ------------------------------------------------------------------ state --
    def _set(self, s: str) -> None:
        if self.muted and s in ("idle", "listening"):
            s = "muted"
        if s != self.state:
            self.state = s
            self.stateChanged.emit(s)

    def _rest_state(self) -> str:
        if self.muted:
            return "muted"
        return "listening" if (self.hands_free and self._accept_audio) else "idle"

    # ------------------------------------------------------------------ models --
    def audio_ok(self) -> tuple[bool, str]:
        return audio.available()

    def models_missing(self) -> list[tuple[str, str]]:
        out = []
        if not models.stt_ready():
            out.append(("stt", models.DEFAULT_STT))
        if not models.tts_ready(self.voice_key()):
            out.append(("tts", self.voice_key()))
        return out

    def download_size_mb(self) -> int:
        return sum((models.STT if k == "stt" else models.TTS)[v]["download_mb"] for k, v in self.models_missing())

    def download(self, then=None) -> None:
        """Fetch missing models in the background (progress signal), then load them."""
        if self._loading:
            return
        self._loading, self._download_cancel = True, False
        self._after_load = then
        self._set("loading")

        def run():
            try:
                missing = self.models_missing()
                for i, (kind, key) in enumerate(missing):
                    label = "speech recognition" if kind == "stt" else "voice"
                    models.ensure(kind, key, lambda d, t, i=i, label=label: self.progress.emit(
                        label, int(d * 100 / t) if t else 0), lambda: self._download_cancel)
                self.progress.emit("", -1)
                self._load_engines()
                self._ready.emit("")
            except models.Cancelled:
                self._ready.emit("Voice download cancelled.")
            except Exception as e:  # noqa: BLE001
                self._ready.emit(f"Voice setup failed: {e}")
        threading.Thread(target=run, daemon=True).start()

    def cancel_download(self) -> None:
        self._download_cancel = True

    def _load_engines(self) -> None:
        if self.stt is None:
            self.stt = engine.Stt()
        if self.tts is None or self.tts.voice != self.voice_key():
            self.tts = engine.Tts(self.voice_key())

    def _on_ready(self, err: str) -> None:
        self._loading = False
        self._set("idle" if not err else "error")
        if err:
            self.notice.emit(err)
            self._after_load = None
            return
        self.notice.emit("Voice is ready.")
        cb, self._after_load = self._after_load, None
        if cb:
            cb()
        else:
            self._set(self._rest_state())

    def ready(self) -> bool:
        return self.stt is not None and self.tts is not None and not self.models_missing()

    def _ensure(self, then) -> bool:
        """True if voice engines are loaded now; else starts download/load and calls `then` when ready."""
        ok, why = self.audio_ok()
        if not ok:
            self.notice.emit(why)
            return False
        if self.ready():
            return True
        if self._loading:
            self.notice.emit("Voice is still loading…")
            return False
        self.download(then)
        return False

    def set_voice(self, key: str) -> None:
        self.settings.set("voice_voice", key)
        if models.tts_ready(key) and self.stt is not None:
            threading.Thread(target=lambda: setattr(self, "tts", engine.Tts(key)), daemon=True).start()

    # ------------------------------------------------------------------ listening --
    def _start_recorder(self) -> bool:
        if self._rec is not None and self._rec.running:
            return True
        self._rec = audio.Recorder(self._on_audio, lambda m: self._err.emit(m))
        return self._rec.start()

    def _stop_recorder(self) -> None:
        if self._rec is not None:
            self._rec.stop()
            self._rec = None

    def _new_utt(self) -> None:
        self._utt = self.stt.stream()
        self._last_partial = ""

    def ptt_begin(self) -> None:
        if not self.enabled or self._ptt:
            return
        if self.muted:
            self.notice.emit("Microphone is muted — unmute to talk.")
            return
        self.stop_speaking()
        self._held = True

        def after_load():
            if self._held:
                self._begin_now()
            else:
                self.notice.emit("Voice is ready: hold the mic button (or the talk key) and speak.")
        if self._ensure(after_load):
            self._begin_now()

    def _begin_now(self) -> None:
        self._ptt = True
        with self._lock:
            self._new_utt()
        self._accept_audio = True
        if not self._start_recorder():
            self._ptt = False
            self._accept_audio = False
            return
        self._set("listening")

    def preload(self) -> None:
        """Load the engines in the background at startup if the models are already on disk (so the first press is instant)."""
        if self.enabled and not self.ready() and not self.models_missing() and self.audio_ok()[0] and not self._loading:
            self.download()

    def ptt_end(self) -> None:
        self._held = False
        if not self._ptt:
            return
        self._ptt = False
        self._accept_audio = self.hands_free
        if not self.hands_free:
            self._stop_recorder()
        with self._lock:
            u, self._utt = self._utt, None
            if self.hands_free:
                self._new_utt()
        if u is None:
            return
        self._set("thinking")
        self.partial.emit("")

        def fin():
            try:
                self._utterance.emit(u.finish() if u.seconds() > 0.25 else "")
            except Exception as e:  # noqa: BLE001
                self._err.emit(f"Speech recognition failed: {e}")
        threading.Thread(target=fin, daemon=True).start()

    def set_hands_free(self, on: bool) -> None:
        if on == self.hands_free:
            return
        if on:
            if self.muted:
                self.notice.emit("Unmute the microphone first.")
                self.handsFreeChanged.emit(False)
                return
            self.hands_free = True
            if not self._ensure(lambda: self.set_hands_free(True)):
                self.hands_free = False
                self.handsFreeChanged.emit(False)
                return
            self.handsFreeChanged.emit(True)
            self._resume_listening()
        else:
            self.hands_free = False
            self.handsFreeChanged.emit(False)
            if not self._ptt:
                self._accept_audio = False
                self._stop_recorder()
                self.partial.emit("")
                if self.state in ("listening",):
                    self._set(self._rest_state())

    def _resume_listening(self) -> None:
        """Hands-free: open the mic for the next utterance (not while grok is answering or speaking)."""
        if not self.hands_free or self.muted or self._ptt:
            return
        if self._turn_open or self._pending > 0 or self.state in ("thinking", "speaking", "loading"):
            return
        with self._lock:
            self._new_utt()
        if not self._start_recorder():
            self.hands_free = False
            self.handsFreeChanged.emit(False)
            return
        self._accept_audio = True
        self._set("listening")

    def set_muted(self, on: bool) -> None:
        if on == self.muted:
            return
        self.muted = on
        self.mutedChanged.emit(on)
        if on:
            self._ptt = False
            self._accept_audio = False
            self._stop_recorder()
            self.partial.emit("")
            self._set("muted")
        else:
            self.state = "idle"
            self.stateChanged.emit("idle")
            if self.hands_free:
                self._resume_listening()

    def _on_audio(self, b: bytes) -> None:       # recorder thread
        if not self._accept_audio or self.muted:
            return
        now = time.time()
        if now - self._level_t > 0.04:
            self._level_t = now
            self.levelChanged.emit(min(1.0, audio.rms(b) * 8))
        with self._lock:
            u = self._utt
            if u is None:
                return
            try:
                u.feed(b)
                txt = u.text()
                if txt and txt != self._last_partial:
                    self._last_partial = txt
                    self.partial.emit(engine.tidy(txt).rstrip(".?!") if txt else "")
                if self.hands_free and not self._ptt and u.endpointed():
                    final = engine.tidy(txt)
                    self._accept_audio = False
                    self._utt = None
                    self._utterance.emit(final)
            except Exception as e:  # noqa: BLE001
                self._err.emit(f"Speech recognition failed: {e}")

    def _on_utterance(self, text: str) -> None:
        self.partial.emit("")
        text = (text or "").strip()
        if len(text.strip(" .?!")) < 2:
            self._set(self._rest_state())
            self._resume_listening()
            if text == "" and self.state == "idle":
                self.notice.emit("I didn't catch anything.")
            return
        self.transcribed.emit(text)
        self._submit(text)

    def _submit(self, text: str) -> None:
        if self.ctl.acp.busy:        # talking over grok = interrupt, then send
            self._pending_text = text
            self.ctl.stop()
            return
        self._set("thinking")
        if not self.ctl.send(text, voice=True):
            self._set(self._rest_state())
            self._resume_listening()

    def _on_error(self, msg: str) -> None:
        self.notice.emit(msg)
        self._accept_audio = False
        self._ptt = False
        self._stop_recorder()
        if self.hands_free:
            self.hands_free = False
            self.handsFreeChanged.emit(False)
        self._set("error" if self._loading else self._rest_state())

    # ------------------------------------------------------------------ speaking --
    def _start_threads(self) -> None:
        if self._threads_started:
            return
        self._threads_started = True
        threading.Thread(target=self._synth_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    def _done_one(self) -> None:
        with self._plock:
            self._pending = max(0, self._pending - 1)
            idle = self._pending == 0
        if idle:
            self._ttsActivity.emit(False)

    def _synth_loop(self) -> None:
        while True:
            epoch, text = self._synth_q.get()
            if epoch != self._epoch:
                self._done_one()
                continue
            try:
                if self.tts is None or self.tts.voice != self.voice_key():
                    self.tts = engine.Tts(self.voice_key())     # first spoken sentence of this run: load the voice
                samples, rate = self.tts.synth(text, self.speed())
            except Exception as e:  # noqa: BLE001
                self._err.emit(f"Speech synthesis failed: {e}")
                self._done_one()
                continue
            self._play_q.put((epoch, samples, rate))

    def _play_loop(self) -> None:
        while True:
            epoch, samples, rate = self._play_q.get()
            if epoch == self._epoch:
                self._ttsActivity.emit(True)
                try:
                    self._player.play(samples, rate)
                except Exception as e:  # noqa: BLE001
                    self._err.emit(f"Audio playback failed: {e}")
            self._done_one()

    def speak(self, text: str) -> None:
        """Speak arbitrary text now (used by the settings 'Test voice' button)."""
        if not self._ensure(lambda: self.speak(text)):
            return
        self._enqueue([text])

    def _enqueue(self, sentences: list[str]) -> None:
        if not sentences:
            return
        self._start_threads()
        for s in sentences:
            with self._plock:
                self._pending += 1
            self._synth_q.put((self._epoch, s))

    def stop_speaking(self) -> None:
        """Stop button: cut off speech and drop everything still queued for this reply."""
        self._epoch += 1
        self._speaking_turn = False
        self._filter.reset()
        for q in (self._synth_q, self._play_q):
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
                self._done_one()
        self._player.stop()

    def skip_sentence(self) -> None:
        self._player.stop()

    def _on_tts_activity(self, speaking: bool) -> None:
        if speaking:
            if self._rec is not None and self._accept_audio and not self._ptt:
                self._accept_audio = False     # half-duplex: don't hear ourselves
            self._set("speaking")
        elif self._turn_open:
            self._set("thinking")
        else:
            self._set(self._rest_state())
            self._resume_listening()

    # chat -> speech
    def _should_speak(self) -> bool:
        m = self.speak_mode()
        return self.enabled and m != "off" and (m == "always" or getattr(self.ctl, "turn_voice", False)) \
            and self.audio_ok()[0] and models.tts_ready(self.voice_key())

    def _on_assistant_text(self, text: str) -> None:
        if not self._turn_open:
            self._turn_open = True
            self._filter.reset()
            self._speaking_turn = self._should_speak()
        if self._speaking_turn:
            self._enqueue(self._filter.feed(text))

    def _on_tool_step(self) -> None:
        if self._speaking_turn:
            self._enqueue(self._filter.step_break())

    def _on_turn_ended(self, reason: str) -> None:
        if self._speaking_turn:
            self._enqueue(self._filter.flush())
        self._turn_open = False
        self._speaking_turn = False
        if self._pending_text:
            t, self._pending_text = self._pending_text, ""
            QTimer.singleShot(150, lambda: self._submit(t))
            return
        if self._pending == 0:
            self._set(self._rest_state())
            self._resume_listening()

    def shutdown(self) -> None:
        self._accept_audio = False
        self._stop_recorder()
        self.stop_speaking()
