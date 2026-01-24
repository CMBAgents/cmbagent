# CMBAgent MCP Integration - Proposal & Implementation Plan

**Date**: 2026-01-24
**Status**: Proposal
**Goal**: Create AG2 agent with human input and MCP tools connecting to CMBAgent backend endpoints

## Executive Summary

**Is this a good idea?** ✅ **YES - Excellent Idea**

This proposal will create a conversational interface to CMBAgent's backend using:
- **AG2 ConversableAgent** with LLM capabilities
- **MCP (Model Context Protocol)** to expose backend endpoints as tools
- **Human-in-the-loop** for task approval and guidance
- **Modular architecture** as a standalone package

## Why This Is a Good Idea

### 1. **Natural Language Interface**
- Users describe tasks in natural language instead of JSON payloads
- LLM agent interprets intent and selects appropriate tools
- More accessible than direct API calls

### 2. **Standardized Tool Protocol**
- MCP is an industry standard for LLM-tool integration
- Easier to maintain than custom tool implementations
- Future-proof architecture

### 3. **Human Oversight**
- User approves tasks before execution
- Can provide guidance during multi-step workflows
- Prevents runaway agent behavior

### 4. **Modular Design**
- Separate module doesn't affect existing codebase
- Can be used alongside existing UI (cmbagent-ui)
- Easy to test and iterate

### 5. **Leverages Existing Infrastructure**
- Backend already has all endpoints defined
- FastAPI → FastMCP conversion is straightforward
- No changes to core CMBAgent needed

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     User (Terminal/CLI)                      │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            │ Natural language input
                            │ Human approval/guidance
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                 AG2 ConversableAgent + UserProxyAgent        │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  AssistantAgent (with LLM)                           │  │
│  │  - Interprets user requests                          │  │
│  │  - Selects appropriate tools                         │  │
│  │  - Orchestrates multi-step workflows                 │  │
│  └──────────────────────────────────────────────────────┘  │
│                            │                                 │
│                            │ Tool calls (MCP protocol)       │
│                            ▼                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  MCP Toolkit (from MCP Server)                       │  │
│  │  - run_one_shot                                      │  │
│  │  - run_deep_research                                 │  │
│  │  - run_idea_generation                               │  │
│  │  - run_ocr                                           │  │
│  │  - run_arxiv_filter                                  │  │
│  │  - run_enhance_input                                 │  │
│  │  - check_credentials                                 │  │
│  │  - get_cost_data                                     │  │
│  │  - get_work_dir_files                                │  │
│  └──────────────────────────────────────────────────────┘  │
│                            │                                 │
│                            │ HTTP POST                       │
│                            ▼                                 │
└─────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────┴─────────────────────────────────┐
│                    MCP Server (FastMCP)                       │
│                                                               │
│  Exposes backend endpoints as MCP tools                      │
│  Transport: stdio (local) or SSE (remote)                    │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             │ HTTP API calls
                             ▼
┌─────────────────────────────────────────────────────────────┐
│              CMBAgent Backend (FastAPI)                      │
│              Running on http://localhost:8000                │
│                                                              │
│  Endpoints:                                                  │
│  - POST /api/one-shot                                        │
│  - POST /api/deep-research                                   │
│  - POST /api/idea-generation                                 │
│  - POST /api/ocr/process                                     │
│  - POST /api/arxiv/filter                                    │
│  - POST /api/enhance-input                                   │
│  - GET /api/credentials/check                                │
│  - GET /api/files/*                                          │
└─────────────────────────────────────────────────────────────┘
```

## Component Breakdown

### 1. MCP Server (`cmbagent_mcp_server.py`)

**Purpose**: Expose CMBAgent backend endpoints as MCP tools

**Technology**: FastMCP (Python MCP SDK)

**Tools to expose**:
```python
@mcp.tool()
async def run_one_shot(
    task: str,
    max_rounds: int = 10,
    max_attempts: int = 3,
    engineer_model: str = "gpt-4o",
    work_dir: str = "./work"
) -> dict:
    """Execute a one-shot engineering task using CMBAgent."""
    # POST to http://localhost:8000/api/one-shot
    # Return results

@mcp.tool()
async def run_deep_research(
    task: str,
    max_rounds: int = 20,
    work_dir: str = "./work"
) -> dict:
    """Execute a deep research task with planning and control."""
    # POST to http://localhost:8000/api/deep-research
    # Return results

@mcp.tool()
async def run_idea_generation(
    task: str,
    max_rounds: int = 15,
    work_dir: str = "./work"
) -> dict:
    """Generate research ideas using CMBAgent."""
    # POST to http://localhost:8000/api/idea-generation
    # Return results

@mcp.tool()
async def run_ocr(
    input_path: str,
    output_dir: str = "./docs_processed",
    mistral_model: str = "pixtral-12b-2409"
) -> dict:
    """Convert PDF to markdown using OCR."""
    # POST to http://localhost:8000/api/ocr/process
    # Return results

@mcp.tool()
async def run_arxiv_filter(
    input_text: str,
    work_dir: str = "./work"
) -> dict:
    """Download arXiv papers from URLs in text."""
    # POST to http://localhost:8000/api/arxiv/filter
    # Return results

@mcp.tool()
async def run_enhance_input(
    text: str,
    max_workers: int = 4,
    work_dir: str = "./work"
) -> dict:
    """Enhance input text with arXiv paper context (download + OCR + summarize)."""
    # POST to http://localhost:8000/api/enhance-input
    # Return results

@mcp.tool()
async def check_api_credentials() -> dict:
    """Check which API keys are configured."""
    # GET from http://localhost:8000/api/credentials/check
    # Return status

@mcp.tool()
async def get_cost_data(work_dir: str) -> dict:
    """Get cost analysis for a completed task."""
    # GET from http://localhost:8000/api/files/cost/cost.json
    # Return cost data

@mcp.tool()
async def list_work_dir_files(work_dir: str) -> list:
    """List files in a work directory."""
    # GET from http://localhost:8000/api/files/
    # Return file list
```

**Transport**: Start with `stdio` (local process communication) for simplicity

**Configuration**:
```python
# Backend URL
BACKEND_URL = "http://localhost:8000"

# Default work directory
DEFAULT_WORK_DIR = "./cmbagent_work"
```

### 2. AG2 Client Agent (`cmbagent_agent.py`)

**Purpose**: Conversational agent that uses MCP tools

**Components**:

#### AssistantAgent (LLM-powered)
```python
from autogen import AssistantAgent, LLMConfig

assistant = AssistantAgent(
    name="cmbagent_assistant",
    system_message="""You are a helpful assistant that helps users run scientific research tasks using CMBAgent.

You have access to several tools for different types of tasks:
- run_one_shot: Quick engineering tasks (code generation, analysis)
- run_deep_research: Complex multi-step research with planning
- run_idea_generation: Generate research ideas and proposals
- run_ocr: Convert PDF papers to markdown
- run_arxiv_filter: Download papers from arXiv URLs
- run_enhance_input: Download papers, OCR them, and summarize (all-in-one)

Always ask the user to confirm before running tasks that may take time or cost money.
After tasks complete, offer to show results, cost data, or generated files.
""",
    llm_config=LLMConfig(
        config_list=[{"model": "gpt-4o", "api_key": os.getenv("OPENAI_API_KEY")}]
    )
)
```

#### UserProxyAgent (Human input)
```python
from autogen import UserProxyAgent

user_proxy = UserProxyAgent(
    name="user",
    human_input_mode="ALWAYS",  # Always ask for human input
    code_execution_config=False,  # No code execution needed
    default_auto_reply="Please continue or ask me for guidance."
)
```

#### MCP Toolkit Registration
```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from autogen.mcp import create_toolkit

async def setup_agent_with_mcp():
    # Connect to MCP server
    server_params = StdioServerParameters(
        command="python",
        args=["cmbagent_mcp_server.py", "stdio"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Create toolkit from MCP tools
            toolkit = await create_toolkit(session=session)

            # Register tools with assistant
            toolkit.register_for_llm(assistant)
            toolkit.register_for_execution(assistant)

            # Start conversation
            result = await user_proxy.a_initiate_chat(
                assistant,
                message="Hello! I need help with a research task.",
                max_turns=20
            )

            return result
```

### 3. CLI Interface (`cmbagent_mcp_cli.py`)

**Purpose**: Command-line interface for the MCP agent

```python
#!/usr/bin/env python
"""CMBAgent MCP CLI - Interactive research assistant"""

import asyncio
import argparse
from cmbagent_mcp.agent import setup_agent_with_mcp

async def main():
    parser = argparse.ArgumentParser(
        description="CMBAgent MCP - Interactive AI research assistant"
    )
    parser.add_argument(
        "--backend-url",
        default="http://localhost:8000",
        help="CMBAgent backend URL"
    )
    parser.add_argument(
        "--work-dir",
        default="./cmbagent_work",
        help="Default work directory for tasks"
    )
    parser.add_argument(
        "--model",
        default="gpt-4o",
        help="LLM model for the assistant"
    )
    args = parser.parse_args()

    print("🚀 CMBAgent MCP Agent starting...")
    print(f"📡 Backend: {args.backend_url}")
    print(f"📁 Work directory: {args.work_dir}")
    print(f"🤖 Model: {args.model}")
    print("\n💬 Chat started. Type 'exit' to quit.\n")

    await setup_agent_with_mcp(
        backend_url=args.backend_url,
        work_dir=args.work_dir,
        model=args.model
    )

if __name__ == "__main__":
    asyncio.run(main())
```

## Module Structure

```
cmbagent/
└── cmbagent_mcp/                    # New module (separate from core)
    ├── __init__.py                  # Package init
    ├── server.py                    # MCP server (FastMCP)
    ├── agent.py                     # AG2 agent setup
    ├── cli.py                       # CLI interface
    ├── config.py                    # Configuration (backend URL, defaults)
    └── tools/                       # Tool definitions
        ├── __init__.py
        ├── execution.py             # one_shot, deep_research, idea_generation
        ├── documents.py             # ocr, arxiv_filter, enhance_input
        └── utils.py                 # credentials, cost_data, files

# Optional: Standalone package
cmbagent-mcp/                        # Separate git repo/package
├── pyproject.toml                   # Package config
├── README.md                        # Documentation
├── cmbagent_mcp/                    # Same structure as above
└── examples/
    ├── simple_task.py               # Example: Run one-shot task
    ├── research_workflow.py         # Example: Multi-step research
    └── paper_analysis.py            # Example: Download + OCR + summarize papers
```

## Step-by-Step Implementation Plan

### Phase 1: MCP Server (Week 1)

**Goal**: Create MCP server that exposes backend endpoints

#### Step 1.1: Project Setup
```bash
# Create new module directory
mkdir -p cmbagent/cmbagent_mcp/tools
cd cmbagent/cmbagent_mcp

# Create __init__.py files
touch __init__.py tools/__init__.py

# Install dependencies
pip install -U ag2[openai,mcp] httpx
```

#### Step 1.2: Create Configuration (`config.py`)
```python
"""Configuration for CMBAgent MCP integration"""
import os
from pathlib import Path

# Backend configuration
BACKEND_URL = os.getenv("CMBAGENT_BACKEND_URL", "http://localhost:8000")
BACKEND_TIMEOUT = 300  # 5 minutes for long-running tasks

# Default work directory
DEFAULT_WORK_DIR = Path(os.getenv("CMBAGENT_WORK_DIR", "./cmbagent_work"))
DEFAULT_WORK_DIR.mkdir(parents=True, exist_ok=True)

# API keys (passed through to backend)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
```

#### Step 1.3: Create Tool Implementations (`tools/execution.py`)
```python
"""Execution tools: one_shot, deep_research, idea_generation"""
import httpx
from typing import Dict, Any
from ..config import BACKEND_URL, BACKEND_TIMEOUT

async def run_one_shot(
    task: str,
    max_rounds: int = 10,
    max_attempts: int = 3,
    engineer_model: str = "gpt-4o",
    work_dir: str | None = None
) -> Dict[str, Any]:
    """Execute a one-shot engineering task using CMBAgent.

    Args:
        task: Task description
        max_rounds: Maximum conversation rounds
        max_attempts: Maximum retry attempts
        engineer_model: LLM model to use
        work_dir: Working directory for outputs

    Returns:
        Task results including status, outputs, and metadata
    """
    async with httpx.AsyncClient(timeout=BACKEND_TIMEOUT) as client:
        response = await client.post(
            f"{BACKEND_URL}/api/one-shot",
            json={
                "task": task,
                "config": {
                    "maxRounds": max_rounds,
                    "maxAttempts": max_attempts,
                    "engineerModel": engineer_model,
                },
                "work_dir": work_dir,
            }
        )
        response.raise_for_status()
        return response.json()

# Similar for run_deep_research, run_idea_generation...
```

#### Step 1.4: Create MCP Server (`server.py`)
```python
"""CMBAgent MCP Server - Exposes backend endpoints as MCP tools"""
import argparse
from mcp.server.fastmcp import FastMCP
from .tools.execution import run_one_shot, run_deep_research, run_idea_generation
from .tools.documents import run_ocr, run_arxiv_filter, run_enhance_input
from .tools.utils import check_api_credentials, get_cost_data

# Initialize MCP server
mcp = FastMCP("CMBAgentServer")

# Register tools
mcp.tool()(run_one_shot)
mcp.tool()(run_deep_research)
mcp.tool()(run_idea_generation)
mcp.tool()(run_ocr)
mcp.tool()(run_arxiv_filter)
mcp.tool()(run_enhance_input)
mcp.tool()(check_api_credentials)
mcp.tool()(get_cost_data)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CMBAgent MCP Server")
    parser.add_argument(
        "transport",
        choices=["stdio", "sse", "streamable-http"],
        help="Transport mode"
    )
    args = parser.parse_args()

    mcp.run(transport=args.transport)
```

#### Step 1.5: Test MCP Server
```python
# test_mcp_server.py
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def test_mcp_server():
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "cmbagent_mcp.server", "stdio"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # List available tools
            tools = await session.list_tools()
            print(f"Available tools: {[t.name for t in tools]}")

            # Test check_api_credentials
            result = await session.call_tool("check_api_credentials", {})
            print(f"Credentials check: {result}")

if __name__ == "__main__":
    asyncio.run(test_mcp_server())
```

### Phase 2: AG2 Agent (Week 2)

**Goal**: Create conversational agent with MCP tools

#### Step 2.1: Create Agent Setup (`agent.py`)
```python
"""AG2 agent with CMBAgent MCP tools"""
import os
from autogen import AssistantAgent, UserProxyAgent, LLMConfig
from autogen.mcp import create_toolkit
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SYSTEM_MESSAGE = """You are a helpful AI research assistant powered by CMBAgent.

You help users with:
- 🔧 Engineering tasks (run_one_shot)
- 🔬 Deep research (run_deep_research)
- 💡 Idea generation (run_idea_generation)
- 📄 PDF to markdown conversion (run_ocr)
- 📚 Downloading arXiv papers (run_arxiv_filter)
- ✨ Enhanced input with paper context (run_enhance_input)

Guidelines:
1. Always summarize what you're about to do before calling tools
2. Ask for confirmation before long-running or costly operations
3. After tasks complete, offer to show results or files
4. Be helpful and explain outputs in simple terms
"""

async def setup_agent_with_mcp(
    backend_url: str = "http://localhost:8000",
    work_dir: str = "./cmbagent_work",
    model: str = "gpt-4o"
):
    """Set up AG2 agent with MCP tools and start conversation."""

    # Create assistant agent
    assistant = AssistantAgent(
        name="cmbagent_assistant",
        system_message=SYSTEM_MESSAGE,
        llm_config=LLMConfig(
            config_list=[{
                "model": model,
                "api_key": os.getenv("OPENAI_API_KEY")
            }]
        )
    )

    # Create user proxy (human input)
    user_proxy = UserProxyAgent(
        name="user",
        human_input_mode="ALWAYS",
        code_execution_config=False,
        default_auto_reply="",
    )

    # Connect to MCP server and register tools
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "cmbagent_mcp.server", "stdio"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Create and register toolkit
            toolkit = await create_toolkit(session=session)
            toolkit.register_for_llm(assistant)
            toolkit.register_for_execution(assistant)

            # Start conversation
            result = await user_proxy.a_initiate_chat(
                assistant,
                message="Hello! I'm ready to help with research tasks.",
                max_turns=50
            )

            return result
```

#### Step 2.2: Create CLI (`cli.py`)
```python
"""CLI for CMBAgent MCP agent"""
import asyncio
import argparse
import sys
from .agent import setup_agent_with_mcp
from .config import BACKEND_URL, DEFAULT_WORK_DIR

async def main():
    parser = argparse.ArgumentParser(
        description="CMBAgent MCP - Interactive AI Research Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start with defaults
  python -m cmbagent_mcp.cli

  # Specify backend and work directory
  python -m cmbagent_mcp.cli --backend http://localhost:8000 --work-dir ./my_work

  # Use different model
  python -m cmbagent_mcp.cli --model gpt-4-turbo
"""
    )
    parser.add_argument(
        "--backend",
        default=BACKEND_URL,
        help=f"CMBAgent backend URL (default: {BACKEND_URL})"
    )
    parser.add_argument(
        "--work-dir",
        default=str(DEFAULT_WORK_DIR),
        help=f"Work directory for outputs (default: {DEFAULT_WORK_DIR})"
    )
    parser.add_argument(
        "--model",
        default="gpt-4o",
        help="LLM model for assistant (default: gpt-4o)"
    )
    args = parser.parse_args()

    # Banner
    print("=" * 70)
    print("🚀 CMBAgent MCP Agent")
    print("=" * 70)
    print(f"📡 Backend:       {args.backend}")
    print(f"📁 Work Dir:      {args.work_dir}")
    print(f"🤖 Model:         {args.model}")
    print("=" * 70)
    print("\n💬 Starting conversation... (Type 'exit' to quit)\n")

    try:
        await setup_agent_with_mcp(
            backend_url=args.backend,
            work_dir=args.work_dir,
            model=args.model
        )
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
```

#### Step 2.3: Package Entry Point (`__init__.py`)
```python
"""CMBAgent MCP - AG2 agent with MCP tools for CMBAgent backend"""

__version__ = "0.1.0"

from .agent import setup_agent_with_mcp
from .config import BACKEND_URL, DEFAULT_WORK_DIR

__all__ = [
    "setup_agent_with_mcp",
    "BACKEND_URL",
    "DEFAULT_WORK_DIR",
]
```

### Phase 3: Testing & Examples (Week 3)

#### Step 3.1: Create Example Scripts

**`examples/simple_task.py`**:
```python
"""Example: Simple engineering task"""
import asyncio
from cmbagent_mcp import setup_agent_with_mcp

async def main():
    # This will start an interactive session
    # User can say: "Create a Python script that plots a sine wave"
    await setup_agent_with_mcp()

if __name__ == "__main__":
    asyncio.run(main())
```

**`examples/research_workflow.py`**:
```python
"""Example: Multi-step research workflow"""
# User workflow:
# 1. "Download papers about CMB polarization: arxiv:2301.12345, arxiv:2302.67890"
#    → Agent uses run_arxiv_filter
# 2. "Convert them to markdown"
#    → Agent uses run_ocr
# 3. "Summarize the key findings"
#    → Agent uses run_enhance_input (already includes OCR + summarization)
# 4. "Generate research ideas based on these papers"
#    → Agent uses run_idea_generation
```

#### Step 3.2: Integration Tests
```python
# tests/test_integration.py
import pytest
from cmbagent_mcp.tools.execution import run_one_shot
from cmbagent_mcp.tools.utils import check_api_credentials

@pytest.mark.asyncio
async def test_check_credentials():
    result = await check_api_credentials()
    assert "results" in result
    assert result["status"] == "success"

@pytest.mark.asyncio
async def test_one_shot_simple_task():
    result = await run_one_shot(
        task="Print 'Hello, World!' in Python",
        max_rounds=3
    )
    assert result["status"] == "success"
    # Check that code was generated
```

### Phase 4: Documentation & Polish (Week 4)

#### Step 4.1: README.md
```markdown
# CMBAgent MCP

Interactive AI research assistant for CMBAgent using AG2 and MCP.

## Features

- 💬 Natural language interface to CMBAgent
- 🛠️ MCP tools for all backend endpoints
- 👤 Human-in-the-loop for task approval
- 🔧 Modular architecture

## Installation

```bash
pip install -U ag2[openai,mcp]
cd cmbagent
```

## Quick Start

1. Start CMBAgent backend:
```bash
cd backend
python run.py
```

2. Start MCP agent:
```bash
python -m cmbagent_mcp.cli
```

3. Chat with the assistant:
```
User: Generate Python code to plot CMB power spectrum
Assistant: I'll create a script that plots the CMB power spectrum.
          This will use run_one_shot. Shall I proceed?
User: yes
Assistant: [Calls run_one_shot tool...]
```

## Available Tools

- `run_one_shot`: Quick engineering tasks
- `run_deep_research`: Multi-step research with planning
- `run_idea_generation`: Generate research ideas
- `run_ocr`: Convert PDFs to markdown
- `run_arxiv_filter`: Download arXiv papers
- `run_enhance_input`: Download + OCR + summarize papers

## Examples

See `examples/` directory for usage patterns.
```

#### Step 4.2: pyproject.toml (if standalone package)
```toml
[project]
name = "cmbagent-mcp"
version = "0.1.0"
description = "AG2 agent with MCP tools for CMBAgent"
requires-python = ">=3.12"
dependencies = [
    "ag2[openai,mcp]>=0.8.5",
    "httpx>=0.27.0",
]

[project.scripts]
cmbagent-mcp = "cmbagent_mcp.cli:main"
```

## Conversation Flow Examples

### Example 1: Simple Task
```
User: Create a Python script that downloads data from Planck