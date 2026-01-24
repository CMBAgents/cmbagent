"""One-shot execution tool for CMBAgent"""
import httpx
from typing import Dict, Any

# Use absolute import to support both -m and direct script execution
try:
    from cmbagent_mcp.config import BACKEND_URL, BACKEND_TIMEOUT
except ImportError:
    import sys
    from pathlib import Path
    # Add parent directory to path if running as script
    parent_dir = Path(__file__).parent.parent.parent
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))
    from cmbagent_mcp.config import BACKEND_URL, BACKEND_TIMEOUT


async def run_one_shot(
    task: str,
    max_rounds: int = 10,
    max_attempts: int = 3,
    engineer_model: str = "gpt-4o",
    work_dir: str | None = None
) -> Dict[str, Any]:
    """Execute a one-shot engineering task using CMBAgent.

    This tool sends a task to the CMBAgent backend for execution using the
    one-shot mode. The task is processed by an AI engineer agent that can
    write code, run tests, and iterate on solutions.

    Args:
        task: Task description in natural language
        max_rounds: Maximum number of conversation rounds (default: 10)
        max_attempts: Maximum number of retry attempts (default: 3)
        engineer_model: LLM model to use for the engineer agent (default: gpt-4o)
        work_dir: Working directory for outputs (default: auto-generated)

    Returns:
        Dictionary containing:
            - status: "success" or "error"
            - message: Status message
            - result: Task execution results (if successful)
            - work_dir: Path to work directory with outputs

    Example:
        result = await run_one_shot(
            task="Create a Python script that plots a sine wave",
            max_rounds=5
        )
    """
    payload = {
        "task": task,
        "config": {
            "mode": "one-shot",
            "maxRounds": max_rounds,
            "maxAttempts": max_attempts,
            "engineerModel": engineer_model,
        }
    }

    if work_dir:
        payload["work_dir"] = work_dir

    async with httpx.AsyncClient(timeout=BACKEND_TIMEOUT) as client:
        try:
            response = await client.post(
                f"{BACKEND_URL}/api/one-shot",
                json=payload
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            return {
                "status": "error",
                "message": f"HTTP error: {str(e)}",
                "error_type": type(e).__name__
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Unexpected error: {str(e)}",
                "error_type": type(e).__name__
            }
