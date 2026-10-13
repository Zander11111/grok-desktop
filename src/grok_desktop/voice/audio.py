"""Microphone capture and speaker playback without native Python extensions (works with any python3).
Linux: `parec`/`pw-record`/`arecord` for the mic, `paplay`/`pw-cat`/`aplay` for playback (PulseAudio or PipeWire).
Windows: `sounddevice` if installed (mic), winsound (playback). Test hooks: GD_VOICE_MIC_WAV=<file> feeds a wav as the
microphone; GD_VOICE_OUT_DIR=<dir> writes spoken audio to wav files instead of the speakers."""
from __future__ import annotations

import array
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import wave

RATE = 16000
CHUNK = 1600  # samples = 100 ms


def _first(*cmds):
    for c in cmds:
        if shutil.which(c[0]):
            return c
    return None


def mic_command():
    return _first(["parec", "--format=s16le", f"--rate={RATE}", "--channels=1", "--latency-msec=60"],
                  ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"],
                  ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "1"])


def play_command(rate: int):
    return _first(["paplay", "--raw", f"--rate={rate}", "--format=s16le", "--channels=1"],
                  ["pw-cat", "-p", "--rate", str(rate), "--channels", "1", "--format", "s16", "-"],
                  ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(rate), "-c", "1"])


def available() -> tuple[bool, str]:
    if os.environ.get("GD_VOICE_MIC_WAV") or os.environ.get("GD_VOICE_OUT_DIR"):
        return True, ""
    if sys.platform.startswith("linux"):
        if not mic_command():
            return False, "No audio tools found. Install pulseaudio-utils (or pipewire-pulse) / alsa-utils."
        return True, ""
    if sys.platform == "win32":
        try:
            import sounddevice  # noqa: F401
            return True, ""
        except ImportError:
            return False, "Windows microphone capture needs the 'sounddevice' package (not bundled yet)."
    return False, "Voice mode supports Linux (and Windows with sounddevice)."


def pcm16_to_floats(b: bytes) -> list[float]:
    a = array.array("h")
    a.frombytes(b[: len(b) // 2 * 2])
    if sys.byteorder == "big":
        a.byteswap()
    return [v / 32768.0 for v in a]


def rms(b: bytes) -> float:
    a = array.array("h")
    a.frombytes(b[: len(b) // 2 * 2])
    if not a:
        return 0.0
    return (sum(v * v for v in a) / len(a)) ** 0.5 / 32768.0


class Recorder:
    """Calls on_audio(bytes_s16le_16k_mono) for every ~100 ms chunk on a background thread until stop()."""

    def __init__(self, on_audio, on_error=lambda m: None, on_end=lambda: None):
        self.on_audio, self.on_error, self.on_end = on_audio, on_error, on_end
        self._proc = None
        self._stop = threading.Event()
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        self._stop.clear()
        wav = os.environ.get("GD_VOICE_MIC_WAV")
        if wav:
            target = self._run_wav
        elif sys.platform.startswith("linux"):
            cmd = mic_command()
            if not cmd:
                self.on_error("No microphone tool found (install pulseaudio-utils).")
                return False
            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            except OSError as e:
                self.on_error(f"Couldn't start the microphone: {e}")
                return False
            target = self._run_proc
        else:
            target = self._run_sd
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        p = self._proc
        if p is not None:
            try:
                p.kill()
            except OSError:
                pass

    def _run_proc(self):
        p = self._proc
        got = False
        try:
            while not self._stop.is_set():
                b = p.stdout.read(CHUNK * 2)
                if not b:
                    break
                got = True
                self.on_audio(b)
        finally:
            try:
                p.kill()
                p.wait(timeout=2)
            except Exception:
                pass
            self._proc = None
            if not got and not self._stop.is_set():
                self.on_error("The microphone gave no audio (is one connected and unmuted?).")
            self.on_end()

    def _run_wav(self):
        try:
            with wave.open(os.environ["GD_VOICE_MIC_WAV"]) as w:
                data = w.readframes(w.getnframes())
                if w.getframerate() != RATE:
                    raise ValueError("test mic wav must be 16 kHz mono s16")
            step = CHUNK * 2
            for i in range(0, len(data), step):
                if self._stop.is_set():
                    break
                self.on_audio(data[i:i + step])
                time.sleep(0.02)   # faster than real time, still chunked
            # then silence, like a quiet room, until stopped
            while not self._stop.is_set():
                self.on_audio(b"\0" * step)
                time.sleep(0.02)
        except Exception as e:
            self.on_error(f"test mic: {e}")
        self.on_end()

    def _run_sd(self):
        try:
            import sounddevice as sd
            with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK) as s:
                while not self._stop.is_set():
                    data, _ = s.read(CHUNK)
                    self.on_audio(bytes(data))
        except Exception as e:
            self.on_error(f"Microphone error: {e}")
        self.on_end()


class Player:
    """Plays one PCM clip at a time; stop() cuts it off. play() blocks until done (call from a worker thread)."""

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._n = 0
        self.out_files: list[str] = []

    def play(self, samples_f32, rate: int) -> bool:
        pcm = array.array("h", (max(-32768, min(32767, int(v * 32767))) for v in samples_f32))
        if sys.byteorder == "big":
            pcm.byteswap()
        raw = pcm.tobytes()
        out = os.environ.get("GD_VOICE_OUT_DIR")
        if out:
            self._n += 1
            path = os.path.join(out, f"speech-{self._n:03d}.wav")
            with wave.open(path, "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
                w.writeframes(raw)
            self.out_files.append(path)
            return True
        if sys.platform.startswith("linux"):
            cmd = play_command(rate)
            if not cmd:
                return False
            with self._lock:
                self._proc = p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                p.stdin.write(raw)
                p.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            p.wait()
            with self._lock:
                self._proc = None
            return True
        if sys.platform == "win32":
            import io
            import winsound
            bio = io.BytesIO()
            with wave.open(bio, "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
                w.writeframes(raw)
            winsound.PlaySound(bio.getvalue(), winsound.SND_MEMORY)
            return True
        return False

    def stop(self) -> None:
        with self._lock:
            p = self._proc
        if p is not None:
            try:
                p.kill()
            except OSError:
                pass
        if sys.platform == "win32":
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
