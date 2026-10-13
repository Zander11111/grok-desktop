#!/usr/bin/env bash
# Grok Desktop (Linux) portable installer. Per-user by default (no root):
#   ./install.sh            install to ~/.local/share/grok-desktop + launcher in ~/.local/bin + menu entry + icons
#   ./install.sh --deps     also install the system helper packages first (apt on Ubuntu/Zorin/Debian, pacman on Arch; uses sudo)
#   ./install.sh --autostart  also start it at login (minimized)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${GD_PREFIX:-$HOME/.local/share/grok-desktop}"
BIN="$HOME/.local/bin"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons"
DEPS=0; AUTOSTART=0
for a in "$@"; do case "$a" in --deps) DEPS=1;; --autostart) AUTOSTART=1;; -h|--help) sed -n 2,6p "$0"; exit 0;; esac; done

command -v python3 >/dev/null || { echo "python3 is required."; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "Python 3.9 or newer is required."; exit 1; }

if [ "$DEPS" = 1 ]; then
  if command -v apt-get >/dev/null; then
    sudo apt-get update
    sudo apt-get install -y xdotool wmctrl libglib2.0-bin xdg-utils libegl1 libgl1 libxkbcommon0 libxkbcommon-x11-0 \
      libdbus-1-3 libfontconfig1 libfreetype6 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
      libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 gnome-screenshot python3-pil x11-utils x11-xserver-utils
  elif command -v pacman >/dev/null; then
    sudo pacman -S --needed --noconfirm xdotool wmctrl glib2 xdg-utils libxkbcommon-x11 xcb-util-cursor xcb-util-image \
      xcb-util-keysyms xcb-util-renderutil xcb-util-wm libglvnd fontconfig python python-pillow xorg-xprop xorg-xrandr \
      gnome-screenshot
  else
    echo "Unknown package manager: install xdotool, wmctrl, gio (glib2), xdg-utils, libxcb-cursor, a screenshot tool (gnome-screenshot/scrot/maim) yourself."
  fi
fi

mkdir -p "$DEST" "$BIN" "$APPS" "$ICONS"
rm -rf "$DEST/lib" "$DEST/app"
cp -r "$HERE/lib" "$HERE/app" "$DEST/"
sed "s|@ROOT@|$DEST|" "$HERE/grok-desktop.sh" > "$BIN/grok-desktop"
chmod 755 "$BIN/grok-desktop"
cp -r "$HERE/icons/hicolor" "$ICONS/"
sed "s|^Exec=grok-desktop|Exec=$BIN/grok-desktop|" "$HERE/grok-desktop.desktop" > "$APPS/grok-desktop.desktop"
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t -f "$ICONS/hicolor" 2>/dev/null || true
command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS" 2>/dev/null || true
[ "$AUTOSTART" = 1 ] && GD_LAUNCHER="$BIN/grok-desktop" "$BIN/grok-desktop" --autostart on || true

echo "Installed Grok Desktop to $DEST"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: $BIN is not on your PATH yet (log out/in, or add it)." ;; esac
missing=""
for c in xdotool wmctrl gio; do command -v $c >/dev/null || missing="$missing $c"; done
command -v gnome-screenshot >/dev/null || command -v maim >/dev/null || command -v scrot >/dev/null || command -v grim >/dev/null || missing="$missing screenshot-tool(gnome-screenshot|scrot|maim)"
[ -n "$missing" ] && echo "Computer control needs these (re-run with --deps, or install yourself):$missing"
echo "Start it from the app menu ('Grok Desktop') or run: grok-desktop"
