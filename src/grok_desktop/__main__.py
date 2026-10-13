import sys


def run() -> int:
    if "--mcp-server" in sys.argv[1:]:
        from .mcp.server import main as mcp_main
        return mcp_main() or 0
    from .app import main
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
