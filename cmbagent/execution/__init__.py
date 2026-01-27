"""
Execution module for CMBAgent.

Contains code executors for local and remote execution.
"""

from .remote_executor import RemoteWebSocketCodeExecutor, RemoteExecutorManager

__all__ = ["RemoteWebSocketCodeExecutor", "RemoteExecutorManager"]
