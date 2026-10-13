"""PyInstaller entry point. `GrokDesktop.exe --mcp-server` runs the computer-control MCP server that grok spawns."""
from grok_desktop.__main__ import run

raise SystemExit(run())
