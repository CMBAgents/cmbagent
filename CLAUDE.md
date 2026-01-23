# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CMBAgent is a multi-agent system for autonomous scientific discovery, powered by AG2 (formerly AutoGen). It's designed for cosmological research with planning and control strategy, featuring specialized agents for different scientific tasks.

The system uses a two-phase approach:
- **Planning**: A planner and plan reviewer design task execution strategies
- **Control**: Step-by-step execution where sub-tasks are handed to specialized agents

## Key Components

### Core Python Package (`cmbagent/`)
- **Entry point**: `cmbagent.cli:main` provides CLI interface via `cmbagent` command
- **Main API**: `cmbagent.one_shot()` - primary function for task execution
- **Agent system**: 50+ specialized agents in `agents/` directory, each with `.py` and `.yaml` configuration
- **RAG agents**: Specialized retrieval-augmented generation agents for scientific tools (CAMB, CLASS, Cobaya, etc.)
- **Context management**: Sophisticated context handling via `context.py` and `hand_offs.py`

### Next.js Web UI (`cmbagent-ui/`)
- Modern React/TypeScript interface with real-time WebSocket communication
- Components: TaskInput, ConsoleOutput, ResultDisplay, FileBrowser
- Custom hook: `useWebSocket.ts` for real-time updates

### FastAPI Backend (`backend/`)
- WebSocket server connecting UI to CMBAgent Python API
- Real-time streaming of execution logs and outputs

## Common Development Commands

### Python Package
```bash
# Install for development
pip install -e .

# Run CLI
cmbagent run

# Run tests (pytest style, located in tests/)
python tests/test_one_shot.py
python tests/test_engineer.py
# Note: Tests are primarily individual Python scripts, not centralized test runner
```

### Next.js UI
```bash
cd cmbagent-ui
npm install
npm run dev        # Development server
npm run build      # Production build
npm run start      # Production server
npm run lint       # ESLint
```

### FastAPI Backend
```bash
cd backend
pip install -r requirements.txt
python run.py      # Start backend server (port 8000)
```

## Architecture Details

### Agent System
- Each agent has a Python implementation and YAML configuration
- Agents are specialized for different tasks: engineering, research, planning, execution, RAG queries
- Response formatters handle output formatting for specific use cases
- Control agents manage workflow orchestration

### Multi-Modal Capabilities
- Plot generation capabilities
- File browser with inline image viewing

### Scientific Focus
- Pre-configured for cosmological analysis tools (CAMB, CLASS, Cobaya, etc.)
- Literature search and keyword extraction (`literature.py`, `aas_keyword_finder`)
- Data retrieval and processing (`data_retriever.py`)

### Work Directory Structure
Tasks create organized output directories:
```
project_dir/
├── chats/          # Conversation history
├── codebase/       # Generated code
├── cost/           # Cost analysis
├── data/           # Generated data and plots
├── time/           # Timing reports
└── context/        # Context files (.pkl)
```

## Testing
- Tests are individual Python scripts in `tests/` directory
- Run specific test files directly: `python tests/test_<name>.py`
- Tests cover various agents and use cases (engineering, research, plotting, etc.)
- No centralized test runner - tests are designed as standalone demonstrations

## Configuration
- API keys required: `OPENAI_API_KEY`, optionally `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`
- Model configurations in `cmbagent/apis/` (JSON files for different providers)
- Agent configurations in individual `.yaml` files alongside Python implementations

## Key APIs
- `cmbagent.one_shot(task, agent='engineer', model='gpt-4o', work_dir=...)` - Main execution API
- Planning and control via specialized agent orchestration
- WebSocket streaming for real-time UI updates