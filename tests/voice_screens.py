"""Voice-mode screenshots + real-package smoke (run with the packaged lib/app on PYTHONPATH, e.g. python3.12, under Xvfb).
Drives the real window with a synthetic mic wav and writes the spoken replies to wavs."""
import os, sys, json, tempfile, time, wave, array
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import pathlib
from helpers import app, fake_env, wait_until, pump
out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["HOME"] = str(tmp)
qa = app()
fake_env(tmp)
spoken = tmp / "spoken"; spoken.mkdir()
os.environ["GD_VOICE_OUT_DIR"] = str(spoken)
os.environ["GD_VOICE_MODELS"] = os.environ.get("GD_VOICE_MODELS", "")
from grok_desktop import paths, theme
from grok_desktop.app import DesktopApp
from grok_desktop.voice import engine
theme.apply(qa)
# synthetic microphone: the voice says a sentence
tts = engine.Tts(); s, r = tts.synth("Okay, so tell me something interesting about the moon.")
ratio = r / 16000
pcm = [int(sum(s[int(i*ratio):max(int(i*ratio)+1, int((i+1)*ratio))] or [0]) / max(1, len(s[int(i*ratio):max(int(i*ratio)+1, int((i+1)*ratio))])) * 32000) for i in range(int(len(s) / ratio))]
a = array.array("h", [0]*6400 + pcm + [0]*32000)
with wave.open(str(tmp / "mic.wav"), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(a.tobytes())
os.environ["GD_VOICE_MIC_WAV"] = str(tmp / "mic.wav")
d = DesktopApp(qa, show=True)
assert wait_until(lambda: d.ctl.state == "ready")
w, v, ctl = d.win, d.win.voice, d.ctl
w.resize(1100, 720); pump(0.5)
def shot(name): pump(0.25); w.grab().save(str(out / name))
shot("voice-1-idle.png")
v.ptt_begin()
assert wait_until(lambda: v.state == "listening", 30); pump(1.2); shot("voice-2-listening.png")
v.ptt_end()
assert wait_until(lambda: v.state == "thinking", 5); shot("voice-3-thinking.png")
assert wait_until(lambda: v.state == "speaking", 30); pump(0.6); shot("voice-4-speaking.png")
assert wait_until(lambda: not ctl.busy and v._pending == 0 and not v._turn_open, 40)
assert wait_until(lambda: v.state == "idle", 5); pump(0.3); shot("voice-5-after.png")
v.set_muted(True); shot("voice-6-muted.png"); v.set_muted(False)
from grok_desktop.ui import SettingsDialog
dlg = SettingsDialog(ctl, w); dlg.show(); pump(0.4); dlg.findChild(__import__("PySide6.QtWidgets").QtWidgets.QScrollArea).verticalScrollBar().setValue(10**6); pump(0.3); dlg.grab().save(str(out / "voice-7-settings.png")); dlg.scroll_to_voice() if hasattr(dlg, "scroll_to_voice") else None
dlg.close()
print(json.dumps({"user": [m["text"] for m in ctl.messages if m["role"] == "user"], "spoken_wavs": sorted(os.listdir(spoken)),
                  "python": sys.version.split()[0]}))
sys.stdout.flush(); d.shutdown(); os._exit(0)
