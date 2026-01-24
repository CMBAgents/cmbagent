"""Test calling the run_one_shot tool through MCP"""
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_one_shot_tool():
    """Test calling run_one_shot through the MCP server."""
    print("🧪 Testing run_one_shot tool call...\n")

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

            # Call the run_one_shot tool
            print("📞 Calling run_one_shot with simple task...")
            print("   Task: 'Create a Python function that adds two numbers'\n")

            try:
                result = await session.call_tool(
                    "run_one_shot",
                    {
                        "task": "Create a Python function that adds two numbers",
                        "max_rounds": 3,
                        "engineer_model": "gpt-4o"
                    }
                )

                print("📬 Response received:")
                print(f"   Content: {result.content}\n")

                # Parse the result
                if result.content:
                    for item in result.content:
                        if hasattr(item, 'text'):
                            import json
                            response_data = json.loads(item.text)
                            print(f"   Status: {response_data.get('status')}")
                            print(f"   Message: {response_data.get('message', 'N/A')}")
                            if response_data.get('work_dir'):
                                print(f"   Work directory: {response_data['work_dir']}")

                print("\n✅ Tool call completed!")

            except Exception as e:
                print(f"\n⚠️  Error (expected if backend not running): {e}")
                print(f"   Error type: {type(e).__name__}")
                print("\nNote: This is expected if the CMBAgent backend is not running.")
                print("To run the backend:")
                print("  cd backend && python run.py")


if __name__ == "__main__":
    asyncio.run(test_one_shot_tool())
