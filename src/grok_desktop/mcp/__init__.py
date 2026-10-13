"""The local MCP tool server ("grok-desktop") that gives grok computer access: files, PowerShell, screenshots,
mouse/keyboard, windows, apps. grok starts it (we pass it in ACP session/new `mcpServers`); every tool call is
checked with the Grok Desktop app over a localhost control link (permission mode, kill switch, indicator)."""
SERVER_NAME = "grok-desktop"
