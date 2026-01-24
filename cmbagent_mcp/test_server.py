"""Test script for CMBAgent MCP Server"""
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_mcp_server():
    """Test the MCP server by connecting and listing available tools."""
    print("🧪 Testing CMBAgent MCP Server...\n")

    # Configure connection to MCP server
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "cmbagent_mcp.server", "stdio"]
    )

    # Connect to server
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize session
            await session.initialize()
            print("✅ Connected to MCP server\n")

            # List available tools
            tools_result = await session.list_tools()
            print(f"📋 Available tools ({len(tools_result.tools)}):")
            for tool in tools_result.tools:
                print(f"\n  Tool: {tool.name}")
                print(f"  Description: {tool.description}")
                if tool.inputSchema:
                    print(f"  Parameters:")
                    props = tool.inputSchema.get("properties", {})
                    for param_name, param_info in props.items():
                        param_type = param_info.get("type", "unknown")
                        param_desc = param_info.get("description", "")
                        print(f"    - {param_name} ({param_type}): {param_desc}")

            print("\n✅ MCP Server test completed successfully!")


if __name__ == "__main__":
    asyncio.run(test_mcp_server())
