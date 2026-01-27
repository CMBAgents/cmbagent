"""
Data models for Firestore persistence.

These Pydantic models define the schema for all documents stored in Firestore.
"""

from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal
from enum import Enum


class ExecutionStatus(str, Enum):
    """Status of a code execution request."""
    PENDING = "pending"      # Sent to frontend, waiting for ack
    ACKED = "acked"          # Frontend acknowledged receipt
    RUNNING = "running"      # Execution in progress
    COMPLETED = "completed"  # Execution finished successfully
    FAILED = "failed"        # Execution failed


class TaskStatus(str, Enum):
    """Status of a task."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FileEntry(BaseModel):
    """
    Metadata about a file in the task's work directory.

    The actual file content lives on the frontend machine.
    Backend only tracks metadata for the file registry.
    """
    path: str                                          # Relative path from work_dir
    size: int                                          # File size in bytes
    created_at: datetime                               # When file was created/modified
    source: Literal["backend", "execution"]            # Who created it
    mime: str | None = None                            # MIME type (image/png, etc.)
    checksum: str | None = None                        # MD5 hash for integrity


class CodeBlock(BaseModel):
    """A block of code to execute."""
    code: str
    language: str  # "python", "bash", "sh"


class Execution(BaseModel):
    """
    Tracks a single code execution request.

    Executions are persisted to Firestore to survive disconnections.
    When frontend reconnects, pending executions are re-sent.
    """
    execution_id: str
    task_id: str
    user_id: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    code_blocks: list[dict]                            # List of {code, language}
    work_dir: str                                      # Frontend work directory
    timeout: int = 86400                               # Timeout in seconds (default 24h)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    deadline: datetime | None = None                   # Optional hard deadline
    acked_at: datetime | None = None                   # When frontend acked
    completed_at: datetime | None = None               # When execution finished
    result: dict | None = None                         # {exit_code, output, code_file}
    files_created: list[dict] | None = None            # Files created during execution

    def to_firestore(self) -> dict:
        """Convert to Firestore-compatible dict."""
        data = self.model_dump(mode="json")
        # Convert datetime objects to ISO strings
        for key in ["created_at", "deadline", "acked_at", "completed_at"]:
            if data.get(key) and isinstance(data[key], str):
                pass  # Already string from mode="json"
            elif data.get(key):
                data[key] = data[key].isoformat()
        return data

    @classmethod
    def from_firestore(cls, data: dict) -> "Execution":
        """Create from Firestore document."""
        # Convert ISO strings back to datetime
        for key in ["created_at", "deadline", "acked_at", "completed_at"]:
            if data.get(key) and isinstance(data[key], str):
                data[key] = datetime.fromisoformat(data[key])
        return cls(**data)


class Task(BaseModel):
    """
    A user's task (research request).

    Tasks contain the prompt, configuration, status, and file registry.
    """
    task_id: str
    user_id: str
    prompt: str                                        # User's task description
    config: dict                                       # Task configuration
    status: TaskStatus = TaskStatus.PENDING
    mode: str = "one-shot"                             # Execution mode
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None                           # Error message if failed
    file_registry: dict[str, dict] = Field(default_factory=dict)  # path -> FileEntry
    total_cost_usd: float = 0.0
    execution_count: int = 0                           # Number of code executions

    def to_firestore(self) -> dict:
        """Convert to Firestore-compatible dict."""
        return self.model_dump(mode="json")

    @classmethod
    def from_firestore(cls, data: dict) -> "Task":
        """Create from Firestore document."""
        return cls(**data)

    def register_file(self, file_info: dict, source: str = "execution"):
        """Add a file to the registry."""
        path = file_info["path"]
        self.file_registry[path] = {
            "path": path,
            "size": file_info.get("size", 0),
            "created_at": datetime.utcnow().isoformat(),
            "source": source,
            "mime": file_info.get("mime"),
            "checksum": file_info.get("checksum"),
        }


class UserProfile(BaseModel):
    """User profile information."""
    uid: str
    email: str
    name: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    plan: Literal["free", "pro", "enterprise"] = "free"

    def to_firestore(self) -> dict:
        return self.model_dump(mode="json")


class UserUsage(BaseModel):
    """User usage statistics."""
    uid: str
    tasks_run: int = 0
    executions_run: int = 0
    total_cost_usd: float = 0.0
    last_active: datetime | None = None

    def to_firestore(self) -> dict:
        return self.model_dump(mode="json")


# WebSocket Message Types

class ExecuteCodeMessage(BaseModel):
    """Message sent to frontend to execute code."""
    type: Literal["execute_code"] = "execute_code"
    execution_id: str
    task_id: str
    work_dir: str
    timeout: int
    code_blocks: list[dict]


class ExecutionAckMessage(BaseModel):
    """Message from frontend acknowledging execution request."""
    type: Literal["execution_ack"] = "execution_ack"
    execution_id: str


class ExecutionResultMessage(BaseModel):
    """Message from frontend with execution result."""
    type: Literal["execution_result"] = "execution_result"
    execution_id: str
    result: dict  # {exit_code, output, code_file}


class FilesCreatedMessage(BaseModel):
    """Message from frontend reporting new files."""
    type: Literal["files_created"] = "files_created"
    execution_id: str
    files: list[dict]  # [{path, size, mime, checksum}]


class TaskSubmitMessage(BaseModel):
    """Message from frontend to submit a new task."""
    type: Literal["task_submit"] = "task_submit"
    task: str
    config: dict
