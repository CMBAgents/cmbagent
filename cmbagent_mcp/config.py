"""Configuration for CMBAgent MCP integration"""
import os
from pathlib import Path

# Backend configuration
BACKEND_URL = os.getenv("CMBAGENT_BACKEND_URL", "http://localhost:8000")
BACKEND_TIMEOUT = 300  # 5 minutes for long-running tasks

# Default work directory
DEFAULT_WORK_DIR = Path(os.getenv("CMBAGENT_WORK_DIR", "./cmbagent_work"))
DEFAULT_WORK_DIR.mkdir(parents=True, exist_ok=True)

# API keys (for reference, backend will use environment variables)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
