"""
Remote WebSocket Code Executor for CMBAgent.

This executor sends code to the frontend for execution instead of running it locally.
It implements the AG2 CodeExecutor interface for seamless integration.
"""

from autogen.coding import CodeExecutor, CodeBlock, CodeResult
from autogen.coding.base import CodeExtractor
from autogen.coding.markdown_code_extractor import MarkdownCodeExtractor
from typing import Callable, Awaitable, Any
from dataclasses import dataclass, field
import asyncio
import threading
import uuid
import logging
import time

logger = logging.getLogger(__name__)


@dataclass
class PendingExecution:
    """Tracks a pending execution waiting for result."""
    execution_id: str
    event: threading.Event
    result: dict | None = None
    error: str | None = None
    code_blocks: list = field(default_factory=list)
    created_at: float = 0.0


class RemoteWebSocketCodeExecutor(CodeExecutor):
    """
    Code executor that delegates execution to a remote frontend via WebSocket.

    This executor:
    1. Sends code blocks to the frontend
    2. Waits for the frontend to execute and return results
    3. Handles timeouts and reconnection scenarios

    Usage:
        async def send_to_frontend(message: dict):
            await websocket.send_json(message)

        executor = RemoteWebSocketCodeExecutor(
            send_callback=send_to_frontend,
            work_dir="~/cmbagent_workdir/task_123",
            timeout=3600  # 1 hour
        )

        # When frontend sends result:
        executor.receive_result(execution_id, result)
    """

    def __init__(
        self,
        send_callback: Callable[[dict], Awaitable[None]],
        work_dir: str = ".",
        timeout: int = 86400,  # 24 hours default
        task_id: str | None = None,
    ):
        """
        Initialize the remote executor.

        Args:
            send_callback: Async function to send messages to frontend
            work_dir: Working directory on the frontend machine
            timeout: Execution timeout in seconds
            task_id: Task ID for tracking purposes
        """
        self._send_callback = send_callback
        self._work_dir = work_dir
        self._timeout = timeout
        self._task_id = task_id or str(uuid.uuid4())
        self._code_extractor = MarkdownCodeExtractor()
        self._pending: dict[str, PendingExecution] = {}
        self._main_loop: asyncio.AbstractEventLoop | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the main event loop for async operations."""
        self._main_loop = loop

    @property
    def code_extractor(self) -> CodeExtractor:
        """Return the code extractor for parsing code from messages."""
        return self._code_extractor

    @property
    def work_dir(self) -> str:
        """Return the working directory."""
        return self._work_dir

    @property
    def timeout(self) -> int:
        """Return the timeout setting."""
        return self._timeout

    def execute_code_blocks(
        self,
        code_blocks: list[CodeBlock]
    ) -> CodeResult:
        """
        Execute code blocks on the remote frontend.

        This method sends the code to the frontend via WebSocket and waits
        for the result using a threading Event for cross-thread synchronization.

        Args:
            code_blocks: List of code blocks to execute

        Returns:
            CodeResult with exit_code and output
        """
        execution_id = str(uuid.uuid4())

        # Prepare the execution request
        request = {
            "type": "execute_code",
            "execution_id": execution_id,
            "task_id": self._task_id,
            "work_dir": self._work_dir,
            "timeout": self._timeout,
            "code_blocks": [
                {"code": block.code, "language": block.language}
                for block in code_blocks
            ]
        }

        logger.info(f"Sending execution request {execution_id} with {len(code_blocks)} code blocks")

        # Create pending execution with threading Event
        pending = PendingExecution(
            execution_id=execution_id,
            event=threading.Event(),
            code_blocks=request["code_blocks"],
            created_at=time.time()
        )
        self._pending[execution_id] = pending

        try:
            # Send the request to frontend via the main event loop
            if self._main_loop and self._main_loop.is_running():
                # Schedule the async send in the main loop
                future = asyncio.run_coroutine_threadsafe(
                    self._send_callback(request),
                    self._main_loop
                )
                # Wait for the send to complete (with a short timeout)
                future.result(timeout=30)
            else:
                # Fallback: try to run in a new loop
                asyncio.run(self._send_callback(request))

            logger.debug(f"Sent execution request {execution_id}")

            # Wait for result using threading Event
            got_result = pending.event.wait(timeout=self._timeout)

            if not got_result:
                logger.warning(f"Execution {execution_id} timed out after {self._timeout}s")
                return CodeResult(
                    exit_code=124,
                    output=f"Execution timed out after {self._timeout} seconds"
                )

            if pending.error:
                logger.warning(f"Execution {execution_id} failed: {pending.error}")
                return CodeResult(
                    exit_code=1,
                    output=f"Execution error: {pending.error}"
                )

            if pending.result:
                logger.info(f"Execution {execution_id} completed with exit_code={pending.result.get('exit_code', -1)}")
                return CodeResult(
                    exit_code=pending.result.get("exit_code", 1),
                    output=pending.result.get("output", "")
                )

            return CodeResult(
                exit_code=1,
                output="No result received"
            )

        except Exception as e:
            logger.error(f"Execution {execution_id} failed: {e}")
            return CodeResult(
                exit_code=1,
                output=f"Execution error: {str(e)}"
            )
        finally:
            self._pending.pop(execution_id, None)

    def receive_result(self, execution_id: str, result: dict) -> bool:
        """
        Receive execution result from frontend.

        This method is called by the WebSocket handler when the frontend
        sends back an execution result. It signals the waiting thread.

        Args:
            execution_id: The execution ID
            result: Dict with {exit_code, output, code_file, files_created}

        Returns:
            True if the result was accepted, False if execution not found
        """
        if execution_id not in self._pending:
            logger.warning(f"Received result for unknown execution {execution_id}")
            return False

        pending = self._pending[execution_id]
        pending.result = result
        pending.event.set()  # Signal the waiting thread
        logger.debug(f"Set result for execution {execution_id}")
        return True

    def receive_error(self, execution_id: str, error: str) -> bool:
        """
        Receive execution error from frontend.

        Args:
            execution_id: The execution ID
            error: Error message

        Returns:
            True if the error was accepted, False if execution not found
        """
        if execution_id not in self._pending:
            logger.warning(f"Received error for unknown execution {execution_id}")
            return False

        pending = self._pending[execution_id]
        pending.error = error
        pending.event.set()  # Signal the waiting thread
        return True

    def get_pending_executions(self) -> list[str]:
        """Get list of pending execution IDs."""
        return list(self._pending.keys())

    def cancel_execution(self, execution_id: str) -> bool:
        """
        Cancel a pending execution.

        Args:
            execution_id: The execution to cancel

        Returns:
            True if cancelled, False if not found
        """
        if execution_id in self._pending:
            pending = self._pending.pop(execution_id)
            pending.error = "Cancelled"
            pending.event.set()
            return True
        return False

    def restart(self) -> None:
        """Reset executor state, cancelling all pending executions."""
        for execution_id in list(self._pending.keys()):
            self.cancel_execution(execution_id)
        self._pending.clear()
        logger.info("Remote executor restarted")


class RemoteExecutorManager:
    """
    Manages remote executors for multiple tasks.

    This is used by the backend to track which executor belongs to which task,
    and to route execution results to the correct executor.
    """

    def __init__(self):
        self._executors: dict[str, RemoteWebSocketCodeExecutor] = {}

    def register(self, task_id: str, executor: RemoteWebSocketCodeExecutor) -> None:
        """Register an executor for a task."""
        self._executors[task_id] = executor

    def unregister(self, task_id: str) -> None:
        """Unregister an executor."""
        if task_id in self._executors:
            self._executors[task_id].restart()
            del self._executors[task_id]

    def get(self, task_id: str) -> RemoteWebSocketCodeExecutor | None:
        """Get executor for a task."""
        return self._executors.get(task_id)

    def route_result(self, task_id: str, execution_id: str, result: dict) -> bool:
        """Route an execution result to the correct executor."""
        executor = self._executors.get(task_id)
        if executor:
            return executor.receive_result(execution_id, result)
        return False

    def route_error(self, task_id: str, execution_id: str, error: str) -> bool:
        """Route an execution error to the correct executor."""
        executor = self._executors.get(task_id)
        if executor:
            return executor.receive_error(execution_id, error)
        return False
