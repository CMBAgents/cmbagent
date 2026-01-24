# CMBAgent MCP Module

AG2-based MCP (Model Context Protocol) integration for CMBAgent backend.

## Status: Phase 1 Complete ✅

### Implemented
- ✅ Module structure (`cmbagent_mcp/`)
- ✅ Configuration (`config.py`)
- ✅ One-shot tool implementation (`tools/one_shot.py`)
- ✅ MCP Server (`server.py`)
- ✅ Test scripts
- ✅ Error handling

## Module Structure

```
cmbagent_mcp/
├── __init__.py              # Package init
├── config.py                # Configuration (backend URL, defaults)
├── server.py                # MCP server (exposes tools)
├── test_server.py           # Test: List available tools
├── test_one_shot_call.py    # Test: Call run_one_shot tool
└── tools/
    ├── __init__.py
    └── one_shot.py          # run_one_shot tool implementation
```

## Quick Start

### 1. Start CMBAgent Backend
```bash
cd backend
python run.py
```

### 2. Test MCP Server
```bash
# List available tools
python -m cmbagent_mcp.test_server

# Test calling run_one_shot
python -m cmbagent_mcp.test_one_shot_call
```

### 3. Run MCP Server Manually
```bash
# stdio transport (for local use)
python -m cmbagent_mcp.server stdio

# SSE transport (for remote use)
python -m cmbagent_mcp.server sse

# streamable-http transport
python -m cmbagent_mcp.server streamable-http
```

## Current Tool: run_one_shot

**Function**: Execute one-shot engineering tasks via CMBAgent backend

**Parameters**:
- `task` (string, required): Task description in natural language
- `max_rounds` (int, default: 10): Maximum conversation rounds
- `max_attempts` (int, default: 3): Maximum retry attempts
- `engineer_model` (string, default: "gpt-4o"): LLM model for engineer agent
- `work_dir` (string, optional): Working directory for outputs

**Returns**:
- `status`: "success" or "error"
- `message`: Status message
- `result`: Task execution results (if successful)
- `work_dir`: Path to work directory with outputs

**Example Call** (via MCP):
```python
result = await session.call_tool(
    "run_one_shot",
    {
        "task": "Create a Python function that adds two numbers",
        "max_rounds": 5,
        "engineer_model": "gpt-4o"
    }
)
```

## Test Results

### ✅ Test 1: MCP Server Connection
```bash
$ python -m cmbagent_mcp.test_server
🧪 Testing CMBAgent MCP Server...
✅ Connected to MCP server
📋 Available tools (1):
  Tool: run_one_shot
  Description: Execute a one-shot engineering task using CMBAgent.
✅ MCP Server test completed successfully!
```

### ✅ Test 2: Tool Call (Backend Running)
```bash
$ python -m cmbagent_mcp.test_one_shot_call
🧪 Testing run_one_shot tool call...
✅ Connected to MCP server
📞 Calling run_one_shot with simple task...
📬 Response received:
   Status: success  # (when backend is running)
✅ Tool call completed!
```

### ✅ Test 3: Error Handling (Backend Not Running)
```bash
$ python -m cmbagent_mcp.test_one_shot_call
📬 Response received:
   Status: error
   Message: HTTP error: Client error '404 Not Found' for url 'http://localhost:8000/api/one-shot'
✅ Tool call completed!
```
Error handling works correctly ✓

## Configuration

Environment variables (in `config.py`):
- `CMBAGENT_BACKEND_URL`: Backend URL (default: `http://localhost:8000`)
- `CMBAGENT_WORK_DIR`: Default work directory (default: `./cmbagent_work`)
- `OPENAI_API_KEY`: OpenAI API key
- `ANTHROPIC_API_KEY`: Anthropic API key
- `MISTRAL_API_KEY`: Mistral API key

## Dependencies

Required packages (already installed):
- `mcp` - Model Context Protocol SDK
- `httpx` - Async HTTP client
- `ag2[mcp]` - AG2 with MCP support (for next phase)

## Next Steps

### Phase 2: Add More Tools
- [ ] `run_deep_research` - Deep research mode
- [ ] `run_idea_generation` - Idea generation mode
- [ ] `run_ocr` - PDF to markdown OCR
- [ ] `run_arxiv_filter` - Download arXiv papers
- [ ] `run_enhance_input` - Download + OCR + summarize

### Phase 3: AG2 Agent Integration
- [ ] Create AssistantAgent with LLM
- [ ] Create UserProxyAgent for human input
- [ ] Register MCP toolkit with agents
- [ ] Implement conversation orchestration
- [ ] Create CLI interface

### Phase 4: Advanced Features
- [ ] WebSocket streaming support
- [ ] Cost tracking integration
- [ ] File browsing tools
- [ ] Multi-step workflows
- [ ] Session management

## Architecture

```
User Input
    ↓
AG2 Agent (Future)
    ↓
MCP Server (server.py)  ← Current Phase
    ↓
Tool: run_one_shot (tools/one_shot.py)
    ↓
HTTP POST → CMBAgent Backend
    ↓
Response
```

## Development Notes

### Adding a New Tool

1. Create tool file in `tools/`:
```python
# tools/my_tool.py
async def my_tool(param1: str, param2: int) -> dict:
    """Tool description"""
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{BACKEND_URL}/api/endpoint", ...)
        return response.json()
```

2. Export in `tools/__init__.py`:
```python
from .my_tool import my_tool
__all__ = [..., "my_tool"]
```

3. Register in `server.py`:
```python
from .tools.my_tool import my_tool
mcp.tool()(my_tool)
```

### Transport Protocols

- **stdio**: Process communication (best for local development)
- **SSE**: Server-Sent Events over HTTP (good for remote servers)
- **streamable-http**: Bidirectional HTTP streaming (for complex APIs)

## Troubleshooting

### MCP Server Won't Start
```bash
# Check if mcp is installed
python -c "import mcp; print('OK')"

# If not, install
pip install -U ag2[mcp]
```

### Tool Call Fails with 404
```bash
# Ensure backend is running
cd backend && python run.py

# Check backend URL in config.py
# Default: http://localhost:8000
```

### Connection Timeout
```bash
# Increase timeout in config.py
BACKEND_TIMEOUT = 600  # 10 minutes
```

## Documentation

- [MCP Specification](https://spec.modelcontextprotocol.io/)
- [AG2 MCP Integration](https://docs.ag2.ai/latest/docs/topics/tools/)
- [FastMCP Documentation](https://github.com/modelcontextprotocol/python-sdk)

## License

Apache-2.0 (same as CMBAgent)
