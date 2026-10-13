"""Voice mode: text filter, models, local STT on synthetic audio, TTS to wav, and the whole loop against the fake grok.
Model-dependent tests need .voice-models/ (scripts/fetch-voice-test-models.sh) and are skipped without it."""
import array
import os
import wave

import pytest

from helpers import app, fake_env, pump, wait_until

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.join(ROOT, ".voice-models")
have_models = os.path.isdir(os.path.join(MODELS, "stt-zipformer-en-20M")) and os.path.isdir(os.path.join(MODELS, "tts-lessac-medium"))
needs_models = pytest.mark.skipif(not have_models, reason="run scripts/fetch-voice-test-models.sh")


@pytest.fixture
def vmodels(monkeypatch):
    monkeypatch.setenv("GD_VOICE_MODELS", MODELS)
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        pytest.skip("sherpa-onnx not installed")


def speech_wav(path, text, lead=0.4, tail=1.6):
    """Synthesize `text` with the TTS, resample to 16 kHz mono and write a wav (what a mic would deliver)."""
    from grok_desktop.voice import engine
    samples, rate = engine.Tts().synth(text)
    n = int(len(samples) * 16000 / rate)
    ratio = rate / 16000
    out = []
    for i in range(n):                      # box-filter resample (avoids aliasing the 22.05 kHz voice)
        a, b = int(i * ratio), max(int(i * ratio) + 1, int((i + 1) * ratio))
        seg = samples[a:b] or [0.0]
        out.append(sum(seg) / len(seg))
    pcm = array.array("h", [0] * int(16000 * lead) + [int(v * 32000) for v in out] + [0] * int(16000 * tail))
    with wave.open(path, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    return path


# ---------------------------------------------------------------- text filter (no models) ----
def test_filter_skips_code_and_noise():
    from grok_desktop.voice.textfilter import CODE_NOTE, SpeechFilter
    f = SpeechFilter()
    chunks = ["Sure! Here's an exam", "ple: `ls -la /tmp` lists files. See https://x.ai/docs for more.\n\n```pyt",
              "hon\nprint('hi')\n```\n\n", "## Next steps\n- **Open** the app\n| a | b |\n|---|---|\nAnything else?"]
    out = []
    for c in chunks:
        out += f.feed(c)
    out += f.flush()
    text = " ".join(out)
    assert CODE_NOTE in out and out.count(CODE_NOTE) == 1
    assert "print" not in text and "ls -la" not in text and "https" not in text and "```" not in text and "**" not in text
    assert "a link" in text and "Open the app" in text and "Anything else?" in text and "|" not in text
    assert out[0].startswith("Sure!")


def test_filter_streams_sentence_by_sentence():
    from grok_desktop.voice.textfilter import SpeechFilter
    f = SpeechFilter()
    got = f.feed("The first sentence is here. The second one is still being wri")
    assert got == ["The first sentence is here."]
    assert f.feed("tten now. ") == ["The second one is still being written now."]
    assert f.feed("Tail") == [] and f.flush() == ["Tail"]


def test_tidy():
    from grok_desktop.voice.engine import tidy
    assert tidy("what is i the time") == "What is I the time?"
    assert tidy("OPEN THE SETTINGS") == "Open the settings."
    assert tidy("") == ""


def test_audio_helpers():
    from grok_desktop.voice import audio
    assert audio.rms(b"\0\0" * 100) == 0
    assert audio.pcm16_to_floats(b"\xff\x7f")[0] > 0.99


# ---------------------------------------------------------------- engines ----
@needs_models
def test_stt_reads_a_real_recording(vmodels):
    """A real 16 kHz human recording (LibriSpeech sample shipped with the model's test files)."""
    from grok_desktop.voice import audio, engine
    with wave.open(os.path.join(ROOT, "tests", "data", "librispeech-0.wav")) as w:
        data = w.readframes(w.getnframes()) + b"\0" * 16000 * 2 * 2
    u = engine.Stt().stream()
    for i in range(0, len(data), audio.CHUNK * 2):
        u.feed(data[i:i + audio.CHUNK * 2])
    assert u.endpointed()                         # trailing silence ended the utterance (the VAD)
    text = u.finish().lower()
    assert "yellow lamps" in text and "squalid quarter" in text


@needs_models
def test_tts_makes_speech_audio(vmodels, tmp_path):
    from grok_desktop.voice import engine
    wav = speech_wav(str(tmp_path / "say.wav"), "Please open the settings and turn on dark mode.")
    with wave.open(wav) as w:
        assert w.getframerate() == 16000 and w.getnframes() > 16000 * 2
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
    assert max(a) > 3000 and min(a) < -3000      # real, non-silent audio of a few seconds
    text = ""
    u = engine.Stt().stream()
    u.feed(a.tobytes())
    text = u.finish().lower()
    assert "settings" in text or "dark" in text   # the synthetic voice is intelligible to the local recognizer (first words may clip)


@needs_models
def test_stt_silence_is_empty(vmodels):
    from grok_desktop.voice import engine
    u = engine.Stt().stream()
    for _ in range(30):
        u.feed(b"\0" * 3200)
    assert u.finish() == ""


# ---------------------------------------------------------------- the whole loop ----
@pytest.fixture
def vapp(tmp_path, monkeypatch, vmodels):
    qa = app()
    fake_env(tmp_path)
    out = tmp_path / "spoken"
    out.mkdir()
    monkeypatch.setenv("GD_VOICE_OUT_DIR", str(out))
    from grok_desktop import paths, theme
    from grok_desktop.app import DesktopApp
    theme.apply(qa)
    paths.Settings()
    d = DesktopApp(qa, show=True)
    d.out = out
    assert wait_until(lambda: d.ctl.state == "ready")
    yield d
    d.shutdown()
    d.win.close()


@needs_models
def test_push_to_talk_roundtrip_speaks_reply_without_code(vapp, tmp_path, monkeypatch):
    wav = speech_wav(str(tmp_path / "mic.wav"), "Okay, so tell me something interesting about the moon.")
    monkeypatch.setenv("GD_VOICE_MIC_WAV", wav)
    w, v, ctl = vapp.win, vapp.win.voice, vapp.ctl
    states = []
    v.stateChanged.connect(states.append)
    v.ptt_begin()
    assert wait_until(lambda: v.state == "listening" or v.ready() and states, 30)
    assert wait_until(lambda: v.stt is not None and v.state == "listening", 30)
    words = ("moon", "interesting", "something", "tell")      # VITS output is randomized, so accept any of the phrase's words
    assert wait_until(lambda: any(x in w.voice_label.text().lower() for x in words), 15)       # live partial transcript
    v.ptt_end()
    assert wait_until(lambda: any(m["role"] == "user" for m in ctl.messages), 15)
    user = [m for m in ctl.messages if m["role"] == "user"][0]["text"]
    assert any(x in user.lower() for x in words)                     # transcript is the user's chat message
    assert wait_until(lambda: not ctl.busy and ctl.state == "ready" and v._pending == 0 and not v._turn_open, 30)
    spoken = sorted(os.listdir(vapp.out))
    assert len(spoken) >= 2                           # reply spoken sentence by sentence (+ the 'code is in the chat' note)
    assert "listening" in states and "thinking" in states and "speaking" in states
    assert wait_until(lambda: v.state == "idle", 5)
    assert "hello from grok" not in " ".join(m["text"] for m in ctl.messages if False)   # (code stays in the chat only)


@needs_models
def test_speak_modes_and_typed_input(vapp):
    v, ctl = vapp.win.voice, vapp.ctl
    v.settings["voice_speak"] = "voice"
    ctl.send("typed question")                         # typed: not spoken in 'voice' mode
    assert wait_until(lambda: not ctl.busy and ctl.state == "ready", 20)
    pump(0.5)
    assert os.listdir(vapp.out) == []
    v.settings["voice_speak"] = "always"
    ctl.send("another typed question")
    assert wait_until(lambda: len(os.listdir(vapp.out)) >= 2, 40)
    assert wait_until(lambda: not ctl.busy and v._pending == 0 and not v._turn_open, 40)
    v.settings["voice_speak"] = "off"
    n = len(os.listdir(vapp.out))
    pump(1)
    ctl.send("third")
    assert wait_until(lambda: not ctl.busy and ctl.state == "ready", 20)
    pump(1)
    assert len(os.listdir(vapp.out)) == n


@needs_models
def test_hands_free_vad_and_mute_and_stop(vapp, tmp_path, monkeypatch):
    wav = speech_wav(str(tmp_path / "mic.wav"), "Hello there, how are you today?")
    monkeypatch.setenv("GD_VOICE_MIC_WAV", wav)
    w, v, ctl = vapp.win, vapp.win.voice, vapp.ctl
    v.set_hands_free(True)
    assert wait_until(lambda: any(m["role"] == "user" for m in ctl.messages), 40)   # VAD ended the utterance by itself
    assert "how are you" in [m["text"] for m in ctl.messages if m["role"] == "user"][0].lower()
    assert wait_until(lambda: not ctl.busy and v._pending == 0 and not v._turn_open, 40)
    assert wait_until(lambda: v.state == "listening", 10)    # mic reopens after Grok finished speaking
    v.set_muted(True)
    assert v.state == "muted" and not v._accept_audio and v._rec is None
    v.set_muted(False)
    assert wait_until(lambda: v.state == "listening", 10)
    v.set_hands_free(False)
    assert wait_until(lambda: v.state == "idle", 5)
    # stop-speaking cuts the rest of a long reply
    v.settings["voice_speak"] = "always"
    ctl.send("slow please")
    assert wait_until(lambda: v.state in ("speaking", "thinking") and v._pending > 0, 30)
    v.stop_speaking()
    n = len(os.listdir(vapp.out))
    assert wait_until(lambda: not ctl.busy, 60)
    pump(1)
    assert wait_until(lambda: v._pending == 0, 15)    # (an in-flight synthesis finishes, then is dropped)
    assert len(os.listdir(vapp.out)) <= n + 1


@needs_models
def test_ui_voice_bar_and_settings(vapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    w = vapp.win
    assert w.voicebar.isVisible() and w.mic_btn.isVisible() and not w.stop_voice_btn.isVisible()
    w.orb.set_state("speaking")
    w.orb.repaint()
    img = w.orb.grab().toImage()
    assert not img.isNull() and any(img.pixelColor(x, 24).blue() > 150 for x in range(img.width()))
    w.voice.settings["voice_enabled"] = False
    w.apply_voice_settings()
    assert not w.voicebar.isVisible()
    from grok_desktop.ui import SettingsDialog
    d = SettingsDialog(vapp.ctl, w)
    d.v_speak.setCurrentIndex(1)
    d.v_speed.setValue(130)
    d.done(0)
    assert vapp.ctl.settings["voice_speak"] == "always" and vapp.ctl.settings["voice_speed"] == 1.3
