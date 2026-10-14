# PyInstaller spec: pyinstaller packaging/grok-desktop.spec  (run from the project root)
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
hidden = (collect_submodules("grok_desktop") + collect_submodules("markdown.extensions")
          + ["pygments.lexers", "pygments.formatters.html", "pygments.styles.monokai", "PySide6.QtNetwork"])
datas, binaries = [], []
try:  # optional voice engine (models download at runtime, never bundled)
    _d, _b, _h = collect_all("sherpa_onnx")
    datas += _d; binaries += _b; hidden += _h
except Exception:
    pass
a = Analysis([os.path.join(SPECPATH, "launcher.py")], pathex=[os.path.join(ROOT, "src")], hiddenimports=hidden,
             datas=datas, binaries=binaries,
             excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
                       "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts",
                       "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="GrokDesktop", console=False,
          icon=os.path.join(SPECPATH, "icon.png" if sys.platform == "darwin" else "icon.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="GrokDesktop")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="Grok Desktop.app", icon=os.path.join(SPECPATH, "icon.png"),
                 bundle_identifier="com.zander11111.grokdesktop", version="0.2.4",
                 info_plist={"NSMicrophoneUsageDescription": "Voice mode listens to you so you can talk to Grok.",
                             "NSHighResolutionCapable": True, "LSUIElement": False})
