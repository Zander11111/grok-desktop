"""Grok Desktop: a ChatGPT-desktop-style app for Windows and Linux that drives the `grok` CLI (Grok Build) over ACP."""
import sys as _sys

__version__ = "0.2.3" if _sys.platform == "win32" else ("0.2.3-macos" if _sys.platform == "darwin" else "0.2.3-linux")
APP_NAME = "Grok Desktop"      # placeholder name; change here + packaging/installer.iss to rename
APP_ID = "GrokDesktop"         # folder name in %APPDATA% and the installer
