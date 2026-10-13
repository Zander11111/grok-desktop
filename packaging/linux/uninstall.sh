#!/usr/bin/env bash
# Remove the per-user install made by install.sh. Your chats/settings (~/.local/share/GrokDesktop) are kept; pass --purge to delete them too.
set -e
DEST="${GD_PREFIX:-$HOME/.local/share/grok-desktop}"
XD="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -rf "$DEST" "$HOME/.local/bin/grok-desktop" "$XD/applications/grok-desktop.desktop" \
       "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/grok-desktop.desktop"
find "$XD/icons/hicolor" -name 'grok-desktop.png' -delete 2>/dev/null || true
[ "${1:-}" = --purge ] && rm -rf "$XD/GrokDesktop"
echo "Grok Desktop removed."
