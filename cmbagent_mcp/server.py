"""CMBAgent MCP Server - Exposes backend endpoints as MCP tools"""
import argparse
import sys
from pathlib import Path

# Add parent directory to path if running as script
if __name__ == "__main__":
    parent_dir = Path(__file__).parent.parent
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))

from mcp.server.fastmcp import FastMCP

# Use absolute import to support both -m and direct script execution
try:
    from cmbagent_mcp.tools.one_shot import run_one_shot
except ImportError:
    from tools.one_shot import run_one_shot

# Initialize MCP server
mcp = FastMCP("CMBAgentServer")

# Register the one_shot tool
mcp.tool()(run_one_shot)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CMBAgent MCP Server")
    parser.add_argument(
        "transport",
        choices=["stdio", "sse", "streamable-http"],
        help="Transport mode (stdio, sse, or streamable-http)"
    )
    args = parser.parse_args()

    print(f"🚀 Starting CMBAgent MCP Server with {args.transport} transport...")
    mcp.run(transport=args.transport)
