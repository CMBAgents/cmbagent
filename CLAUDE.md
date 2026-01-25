# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CMBAgent is an autonomous research backend powered by AG2 (formerly AutoGen). It's a multi-agent system designed for scientific discovery across scientific domains. The system follows a planning and control strategy with specialized agents for different research workflows.

The system uses a two-phase approach:
- **Planning**: A planner and plan reviewer design task execution strategies
- **Control**: Step-by-step execution where sub-tasks are handed to specialized agents

## Key Components

### Core Python Package (`cmbagent/`)
- **Entry point**: `cmbagent.cli:main` provides CLI interface via `cmbagent` command
- **Main API**: `cmbagent.one_shot()` - primary function for task execution
- **Agent system**: Specialized agents in `agents/` directory, each with `.py` and `.yaml` configuration
- **Agent types**: Planning, control, coding (engineer, executor), research (researcher, summarizer), hypothesis generation, keyword extraction, and domain-specific templates
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
- Agents are specialized for different workflows: engineering, research, planning, execution, hypothesis generation, keyword extraction
- Response formatters handle output formatting for specific use cases
- Controller manages workflow orchestration
- Domain-specific agents in `specialized/` serve as templates for users to add their own

### Multi-Modal Capabilities
- Plot generation capabilities
- File browser with inline image viewing

### Research Capabilities
- Domain-agnostic architecture supporting any scientific field
- Literature search and keyword extraction (`literature.py`, `aas_keyword_finder`)
- Example domain packages provided: materials science, biochemistry, astronomy, data science
- Users can easily add their own domain-specific dependencies in `pyproject.toml`

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