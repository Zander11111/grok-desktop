#!/usr/bin/env bash
# Cross-build GrokDesktop.exe + the per-user installer on a Linux box with wine (adapted from grok-app-studio).
# Needs: wine, wine64, wine32:i386, xvfb, curl, unzip. Uses Windows Python 3.12 from the python NuGet package and
# Inno Setup 6.7.3. PySide6 is pinned to 6.6.3.1 because Qt >= 6.7 needs Windows' system ICU, which wine lacks.
# On a real Windows PC use scripts\build-windows.ps1 instead.
set -euo pipefail
cd "$(dirname "$0")/.."
export WINEDEBUG=-all
W=/tmp/gd-winbuild; mkdir -p $W
export WINEPREFIX=${WINEPREFIX:-$HOME/.wine-gd-build}
PY='C:\Python312\python.exe'
if [ ! -f "$WINEPREFIX/drive_c/Python312/python.exe" ]; then
  wineboot -i >/dev/null 2>&1 || true
  curl -fsSL -o $W/python.nupkg https://www.nuget.org/api/v2/package/python/3.12.10
  rm -rf $W/py && mkdir $W/py && (cd $W/py && unzip -q ../python.nupkg)
  mkdir -p "$WINEPREFIX/drive_c/Python312" && cp -r $W/py/tools/* "$WINEPREFIX/drive_c/Python312/"
  wine "$PY" -m pip install -q --no-warn-script-location "PySide6-Essentials==6.6.3.1" pyinstaller markdown pygments
fi
[ -f packaging/icon.ico ] || python3 scripts/make_icon.py
rm -rf dist/GrokDesktop build/pyinstaller
wine "$PY" -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist \
  --workpath build/pyinstaller packaging/grok-desktop.spec
if [ ! -f "$WINEPREFIX/drive_c/InnoSetup/ISCC.exe" ]; then
  curl -fsSL -o $W/inno.exe https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe
  xvfb-run -a wine $W/inno.exe /VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER /NORESTART '/DIR=C:\InnoSetup' >/dev/null 2>&1
fi
wine 'C:\InnoSetup\ISCC.exe' /Q packaging/installer.iss
ls -la dist/*.exe
sha256sum dist/GrokDesktop-Setup-*.exe
