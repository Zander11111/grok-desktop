#!/usr/bin/env bash
# Build the Linux packages into dist/:  grok-desktop_0.2.3-linux_amd64.deb  and  grok-desktop-0.2.3-linux-x86_64.tar.gz
# (needs: .venv with pip, dpkg-deb, tar). Nothing is installed or published.
set -euo pipefail
cd "$(dirname "$0")/.."
VER=0.2.3-linux
TARVER=0.2.3-linux
B=build/linux
rm -rf "$B"; mkdir -p "$B" dist
[ -d packaging/linux/icons ] || .venv/bin/python scripts/make_linux_icons.py
scripts/vendor-linux.sh "$B/lib"

stage_app() {  # $1 = dest dir for the grok_desktop package
  mkdir -p "$1"
  cp -r src/grok_desktop "$1/"
  find "$1" -name '__pycache__' -type d -prune -exec rm -rf {} +
}

# ---------------------------------------------------------------- .deb ----
D="$B/deb"
mkdir -p "$D/DEBIAN" "$D/usr/lib/grok-desktop" "$D/usr/bin" "$D/usr/share/applications" "$D/usr/share/icons" \
  "$D/usr/share/doc/grok-desktop"
cp -r "$B/lib" "$D/usr/lib/grok-desktop/lib"
stage_app "$D/usr/lib/grok-desktop/app"
sed 's|@ROOT@|/usr/lib/grok-desktop|' packaging/linux/grok-desktop.sh > "$D/usr/bin/grok-desktop"
chmod 755 "$D/usr/bin/grok-desktop"
cp packaging/linux/grok-desktop.desktop "$D/usr/share/applications/"
cp -r packaging/linux/icons/hicolor "$D/usr/share/icons/"
cp README.md "$D/usr/share/doc/grok-desktop/README.md"
printf 'Grok Desktop for Linux %s\nCopyright Zander11111 (https://github.com/Zander11111/grok-desktop). Bundles PySide6/Qt (LGPLv3), Markdown (BSD), Pygments (BSD), python-xlib (LGPL), sherpa-onnx + onnxruntime (Apache-2.0/MIT).\nVoice models downloaded on first use: Zipformer ASR (Apache-2.0), Piper voices (MIT/CC).\n' "$VER" > "$D/usr/share/doc/grok-desktop/copyright"
SIZE=$(du -sk "$D" | cut -f1)
cat > "$D/DEBIAN/control" <<CTL
Package: grok-desktop
Version: $VER
Section: net
Priority: optional
Architecture: amd64
Installed-Size: $SIZE
Maintainer: Zander11111 <161892552+Zander11111@users.noreply.github.com>
Depends: python3 (>= 3.9), pulseaudio-utils, xdotool, wmctrl, libglib2.0-bin, xdg-utils, libegl1, libgl1, libxkbcommon0, libxkbcommon-x11-0, libdbus-1-3, libfontconfig1, libfreetype6, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xinerama0, libxcb-xkb1
Recommends: git, gh, gnome-screenshot | maim | scrot, python3-pil, x11-utils, x11-xserver-utils
Suggests: ydotool, grim, kdotool, imagemagick
Homepage: https://x.ai/cli
Description: Chat app for Grok that can use your computer
 A ChatGPT-desktop-style app that talks to xAI's "grok" command-line app (signed in
 with your SuperGrok account; no API key) over the Agent Client Protocol. Chat history,
 markdown and code blocks, attachments, model picker, tray icon, a global quick-ask
 hotkey and optional computer control (files, shell, screenshots, mouse and keyboard,
 windows, apps) with permission prompts and an emergency stop (Ctrl+Alt+Esc).
 GitHub window: sign in (via gh), clone, commit, push, create a repo — each with a confirmation.
 Voice mode (talk to Grok and hear it answer) runs offline with sherpa-onnx; its speech models (~190 MB) download on first use.
 Bundles PySide6/Qt so it also runs on Ubuntu 24.04 / Zorin OS 18, which have no PySide6 package.
CTL
cat > "$D/DEBIAN/postinst" <<'SH'
#!/bin/sh
set -e
python3 -m compileall -q /usr/lib/grok-desktop/app >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications 2>/dev/null || true
exit 0
SH
cat > "$D/DEBIAN/postrm" <<'SH'
#!/bin/sh
set -e
[ "$1" = remove ] || [ "$1" = purge ] && rm -rf /usr/lib/grok-desktop
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications 2>/dev/null || true
exit 0
SH
chmod 755 "$D/DEBIAN/postinst" "$D/DEBIAN/postrm"
find "$D" -type d -exec chmod 755 {} +
find "$D/usr" -type f ! -perm /111 -exec chmod 644 {} +
dpkg-deb --root-owner-group -Zxz -b "$D" "dist/grok-desktop_${VER}_amd64.deb"

# ------------------------------------------------------------- tarball ----
T="$B/tar/grok-desktop-$TARVER"
mkdir -p "$T"
cp -r "$B/lib" "$T/lib"
stage_app "$T/app"
cp -r packaging/linux/icons "$T/icons"
cp packaging/linux/grok-desktop.desktop packaging/linux/grok-desktop.sh packaging/linux/install.sh packaging/linux/uninstall.sh "$T/"
cp README.md "$T/README.md"
chmod 755 "$T/install.sh" "$T/uninstall.sh"
tar -C "$B/tar" --owner=0 --group=0 -czf "dist/grok-desktop-$TARVER-x86_64.tar.gz" "grok-desktop-$TARVER"
( cd dist && sha256sum "grok-desktop_${VER}_amd64.deb" "grok-desktop-$TARVER-x86_64.tar.gz" > SHA256SUMS-linux.txt )
ls -la dist
