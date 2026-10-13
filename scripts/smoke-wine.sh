#!/usr/bin/env bash
# Smoke-test the built installer in a *fresh* wine prefix (no Python inside): silent per-user install,
# the MCP server exe against wine's desktop, then the app itself with the real Windows grok.exe (not signed in).
set -uo pipefail
cd "$(dirname "$0")/.."
EV=docs/evidence; mkdir -p $EV
export WINEDEBUG=-all WINEPREFIX=${SMOKE_PREFIX:-$HOME/.wine-gd-smoke}
rm -rf "$WINEPREFIX"
Xvfb :77 -screen 0 1600x900x24 >/dev/null 2>&1 & XPID=$!
export DISPLAY=:77
sleep 1
wineboot -i >/dev/null 2>&1
U="$WINEPREFIX/drive_c/users/$USER"
echo "== install (per-user, no admin)"
wine dist/GrokDesktop-Setup-0.1.0.exe /VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER /NORESTART /TASKS=desktopicon /LOG='C:\gd-install.log'
echo "installer exit=$?"
APP="$U/AppData/Local/Programs/GrokDesktop"
ls -la "$APP/GrokDesktop.exe" "$APP/unins000.exe" 2>&1
find "$U" -iname 'Grok Desktop*.lnk' 2>/dev/null
wine reg query 'HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{58D82AE6-4380-42E8-8ABD-C2C9125D7BC4}_is1' /v InstallLocation 2>/dev/null | tr -d '\r' | grep -i install
cp "$WINEPREFIX/drive_c/gd-install.log" $EV/wine-install.log 2>/dev/null
echo "== MCP server exe under wine"
timeout 240 python3 tests/wine_mcp_probe.py "$APP/GrokDesktop.exe" $EV
wineserver -k; sleep 1
echo "== app with the real Windows grok.exe (not signed in)"
mkdir -p "$U/bin" && cp /tmp/gd/grok-win.exe "$U/bin/grok.exe"
GD_GROK_BIN='C:\users\'"$USER"'\bin\grok.exe' wine "$APP/GrokDesktop.exe" >/dev/null 2>&1 &
sleep ${APP_WAIT:-45}
import -window root $EV/wine-app.png
cp "$U/AppData/Roaming/GrokDesktop/grok-desktop.log" $EV/wine-app.log 2>/dev/null
ls "$U/AppData/Roaming/GrokDesktop/" 2>&1
wineserver -k
kill $XPID
echo SMOKE_DONE
