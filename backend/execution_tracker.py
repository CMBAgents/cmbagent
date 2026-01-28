"""
Execution Tracker for CMBAgent Backend.

Tracks code execution requests in Firestore for persistence
across WebSocket disconnections and reconnections.
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional
from models import Execution, ExecutionStatus, Task, TaskStatus, FileEntry

logger = logging.getLogger(__name__)

# Check if we're in local development mode
LOCAL_DEV_MODE = os.environ.get("CMBAGENT_LOCAL_DEV", "true").lower() == "true"

# Firestore client (only import if not in local dev mode)
firestore_client = None
if not LOCAL_DEV_MODE:
    try:
        from google.cloud import firestore
        firestore_client = firestore.AsyncClient()
        logger.info("Firestore client initialized")
    except ImportError:
        logger.warning("google-cloud-firestore not installed, using local storage")
        LOCAL_DEV_MODE = True
    except Exception as e:
        logger.warning(f"Failed to initialize Firestore: {e}")
        LOCAL_DEV_MODE = True


class LocalStorage:
    """
    In-memory storage for local development.
    Mimics Firestore interface for testing.
    """

    def __init__(self):
        self.executions: dict[str, dict] = {}
        self.tasks: dict[str, dict] = {}
        self.users: dict[str, dict] = {}

    async def get_execution(self, execution_id: str) -> Optional[dict]:
        return self.executions.get(execution_id)

    async def set_execution(self, execution_id: str, data: dict):
        self.executions[execution_id] = data

    async def update_execution(self, execution_id: str, updates: dict):
        if execution_id in self.executions:
            self.executions[execution_id].update(updates)

    async def delete_execution(self, execution_id: str):
        self.executions.pop(execution_id, None)

    async def get_task(self, task_id: str) -> Optional[dict]:
        return self.tasks.get(task_id)

    async def set_task(self, task_id: str, data: dict):
        self.tasks[task_id] = data

    async def update_task(self, task_id: str, updates: dict):
        if task_id in self.tasks:
            self.tasks[task_id].update(updates)

    async def query_executions(self, **filters) -> list[dict]:
        results = []
        for exec_data in self.executions.values():
            match = True
            for key, value in filters.items():
                if isinstance(value, list):
                    # "in" query
                    if exec_data.get(key) not in value:
                        match = False
                        break
                elif exec_data.get(key) != value:
                    match = False
                    break
            if match:
                results.append(exec_data)
        return results


# Global local storage instance
local_storage = LocalStorage() if LOCAL_DEV_MODE else None


class ExecutionTracker:
    """
    Tracks execution requests and their status.

    In production, uses Firestore for persistence.
    In development, uses in-memory storage.
    """

    def __init__(self):
        self.local = LOCAL_DEV_MODE
        self.db = firestore_client

    async def create_execution(
        self,
        execution_id: str,
        task_id: str,
        user_id: str,
        code_blocks: list[dict],
        work_dir: str,
        timeout: int = 86400,
        deadline_hours: Optional[int] = None,
    ) -> Execution:
        """
        Create a new execution record.

        Args:
            execution_id: Unique execution ID
            task_id: Parent task ID
            user_id: User who owns this execution
            code_blocks: List of code blocks to execute
            work_dir: Working directory on frontend
            timeout: Execution timeout in seconds
            deadline_hours: Optional hard deadline in hours

        Returns:
            Created Execution object
        """
        execution = Execution(
            execution_id=execution_id,
            task_id=task_id,
            user_id=user_id,
            status=ExecutionStatus.PENDING,
            code_blocks=code_blocks,
            work_dir=work_dir,
            timeout=timeout,
            created_at=datetime.utcnow(),
            deadline=datetime.utcnow() + timedelta(hours=deadline_hours) if deadline_hours else None,
        )

        if self.local:
            await local_storage.set_execution(execution_id, execution.to_firestore())
        else:
            doc_ref = self.db.collection("executions").document(execution_id)
            await doc_ref.set(execution.to_firestore())

        logger.info(f"Created execution {execution_id} for task {task_id}")
        return execution

    async def get_execution(self, execution_id: str) -> Optional[Execution]:
        """Get an execution by ID."""
        if self.local:
            data = await local_storage.get_execution(execution_id)
            return Execution.from_firestore(data) if data else None
        else:
            doc_ref = self.db.collection("executions").document(execution_id)
            doc = await doc_ref.get()
            if doc.exists:
                return Execution.from_firestore(doc.to_dict())
            return None

    async def mark_acked(self, execution_id: str) -> bool:
        """
        Mark execution as acknowledged by frontend.

        Returns True if updated, False if not found.
        """
        updates = {
            "status": ExecutionStatus.ACKED.value,
            "acked_at": datetime.utcnow().isoformat(),
        }

        if self.local:
            if execution_id in local_storage.executions:
                await local_storage.update_execution(execution_id, updates)
                logger.debug(f"Execution {execution_id} marked as acked")
                return True
            return False
        else:
            doc_ref = self.db.collection("executions").document(execution_id)
            doc = await doc_ref.get()
            if doc.exists:
                await doc_ref.update(updates)
                logger.debug(f"Execution {execution_id} marked as acked")
                return True
            return False

    async def mark_running(self, execution_id: str) -> bool:
        """Mark execution as running."""
        updates = {
            "status": ExecutionStatus.RUNNING.value,
        }

        if self.local:
            if execution_id in local_storage.executions:
                await local_storage.update_execution(execution_id, updates)
                return True
            return False
        else:
            doc_ref = self.db.collection("executions").document(execution_id)
            doc = await doc_ref.get()
            if doc.exists:
                await doc_ref.update(updates)
                return True
            return False

    async def mark_completed(
        self,
        execution_id: str,
        result: dict,
        files_created: Optional[list[dict]] = None,
    ) -> bool:
        """
        Mark execution as completed with result.

        Args:
            execution_id: Execution ID
            result: Result dict with {exit_code, output, code_file}
            files_created: Optional list of files created

        Returns:
            True if updated, False if not found
        """
        updates = {
            "status": ExecutionStatus.COMPLETED.value,
            "completed_at": datetime.utcnow().isoformat(),
            "result": result,
        }
        if files_created:
            updates["files_created"] = files_created

        if self.local:
            if execution_id in local_storage.executions:
                await local_storage.update_execution(execution_id, updates)
                logger.info(f"Execution {execution_id} completed with exit_code={result.get('exit_code')}")
                return True
            return False
        else:
            doc_ref = self.db.collection("executions").document(execution_id)
            doc = await doc_ref.get()
            if doc.exists:
                await doc_ref.update(updates)
                logger.info(f"Execution {execution_id} completed with exit_code={result.get('exit_code')}")
                return True
            return False

    async def mark_failed(self, execution_id: str, error: str) -> bool:
        """Mark execution as failed."""
        updates = {
            "status": ExecutionStatus.FAILED.value,
            "completed_at": datetime.utcnow().isoformat(),
            "result": {"exit_code": 1, "output": f"Error: {error}"},
        }

        if self.local:
            if execution_id in local_storage.executions:
                await local_storage.update_execution(execution_id, updates)
                logger.warning(f"Execution {execution_id} failed: {error}")
                return True
            return False
        else:
            doc_ref = self.db.collection("executions").document(execution_id)
            doc = await doc_ref.get()
            if doc.exists:
                await doc_ref.update(updates)
                logger.warning(f"Execution {execution_id} failed: {error}")
                return True
            return False

    async def get_pending_for_task(self, task_id: str) -> list[Execution]:
        """
        Get all pending/acked executions for a task.

        Used on reconnection to re-send execution requests.
        """
        pending_statuses = [ExecutionStatus.PENDING.value, ExecutionStatus.ACKED.value]

        if self.local:
            results = await local_storage.query_executions(
                task_id=task_id,
                status=pending_statuses,
            )
            return [Execution.from_firestore(r) for r in results]
        else:
            query = (
                self.db.collection("executions")
                .where("task_id", "==", task_id)
                .where("status", "in", pending_statuses)
            )
            docs = query.stream()
            executions = []
            async for doc in docs:
                executions.append(Execution.from_firestore(doc.to_dict()))
            return executions

    async def get_pending_for_user(self, user_id: str) -> list[Execution]:
        """Get all pending executions for a user."""
        pending_statuses = [ExecutionStatus.PENDING.value, ExecutionStatus.ACKED.value]

        if self.local:
            results = await local_storage.query_executions(
                user_id=user_id,
                status=pending_statuses,
            )
            return [Execution.from_firestore(r) for r in results]
        else:
            query = (
                self.db.collection("executions")
                .where("user_id", "==", user_id)
                .where("status", "in", pending_statuses)
            )
            docs = query.stream()
            executions = []
            async for doc in docs:
                executions.append(Execution.from_firestore(doc.to_dict()))
            return executions

    async def cleanup_old_executions(self, max_age_days: int = 7) -> int:
        """
        Remove old completed/failed executions.

        Returns number of deleted records.
        """
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        deleted = 0

        if self.local:
            to_delete = []
            for exec_id, data in local_storage.executions.items():
                if data.get("status") in [ExecutionStatus.COMPLETED.value, ExecutionStatus.FAILED.value]:
                    completed_at = data.get("completed_at")
                    if completed_at and datetime.fromisoformat(completed_at) < cutoff:
                        to_delete.append(exec_id)

            for exec_id in to_delete:
                await local_storage.delete_execution(exec_id)
                deleted += 1
        else:
            # Firestore batch delete
            query = (
                self.db.collection("executions")
                .where("status", "in", [ExecutionStatus.COMPLETED.value, ExecutionStatus.FAILED.value])
                .where("completed_at", "<", cutoff.isoformat())
            )
            docs = query.stream()
            async for doc in docs:
                await doc.reference.delete()
                deleted += 1

        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old executions")

        return deleted


class TaskTracker:
    """
    Tracks tasks and their file registries.
    """

    def __init__(self):
        self.local = LOCAL_DEV_MODE
        self.db = firestore_client

    async def create_task(
        self,
        task_id: str,
        user_id: str,
        prompt: str,
        config: dict,
        mode: str = "one-shot",
    ) -> Task:
        """Create a new task."""
        task = Task(
            task_id=task_id,
            user_id=user_id,
            prompt=prompt,
            config=config,
            mode=mode,
            status=TaskStatus.PENDING,
            created_at=datetime.utcnow(),
        )

        if self.local:
            await local_storage.set_task(task_id, task.to_firestore())
        else:
            doc_ref = self.db.collection("tasks").document(task_id)
            await doc_ref.set(task.to_firestore())

        logger.info(f"Created task {task_id} for user {user_id}")
        return task

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        if self.local:
            data = await local_storage.get_task(task_id)
            return Task.from_firestore(data) if data else None
        else:
            doc_ref = self.db.collection("tasks").document(task_id)
            doc = await doc_ref.get()
            if doc.exists:
                return Task.from_firestore(doc.to_dict())
            return None

    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        error: Optional[str] = None,
    ) -> bool:
        """Update task status."""
        updates = {"status": status.value}
        if status == TaskStatus.RUNNING:
            updates["started_at"] = datetime.utcnow().isoformat()
        elif status in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
            updates["completed_at"] = datetime.utcnow().isoformat()
        if error:
            updates["error"] = error

        if self.local:
            if task_id in local_storage.tasks:
                await local_storage.update_task(task_id, updates)
                return True
            return False
        else:
            doc_ref = self.db.collection("tasks").document(task_id)
            doc = await doc_ref.get()
            if doc.exists:
                await doc_ref.update(updates)
                return True
            return False

    async def register_files(
        self,
        task_id: str,
        files: list[dict],
        source: str = "execution",
    ) -> bool:
        """
        Register files in the task's file registry.

        Args:
            task_id: Task ID
            files: List of file info dicts
            source: File source ("backend" or "execution")

        Returns:
            True if updated, False if task not found
        """
        if self.local:
            task_data = await local_storage.get_task(task_id)
            if not task_data:
                return False

            file_registry = task_data.get("file_registry", {})
            for file_info in files:
                path = file_info["path"]
                file_registry[path] = {
                    "path": path,
                    "size": file_info.get("size", 0),
                    "created_at": datetime.utcnow().isoformat(),
                    "source": source,
                    "mime": file_info.get("mime"),
                    "checksum": file_info.get("checksum"),
                }

            await local_storage.update_task(task_id, {"file_registry": file_registry})
            logger.debug(f"Registered {len(files)} files for task {task_id}")
            return True
        else:
            doc_ref = self.db.collection("tasks").document(task_id)
            doc = await doc_ref.get()
            if not doc.exists:
                return False

            task_data = doc.to_dict()
            file_registry = task_data.get("file_registry", {})

            for file_info in files:
                path = file_info["path"]
                file_registry[path] = {
                    "path": path,
                    "size": file_info.get("size", 0),
                    "created_at": datetime.utcnow().isoformat(),
                    "source": source,
                    "mime": file_info.get("mime"),
                    "checksum": file_info.get("checksum"),
                }

            await doc_ref.update({"file_registry": file_registry})
            logger.debug(f"Registered {len(files)} files for task {task_id}")
            return True

    async def get_file_registry(self, task_id: str) -> dict:
        """Get the file registry for a task."""
        task = await self.get_task(task_id)
        return task.file_registry if task else {}

    async def update_task_cost(self, task_id: str, total_cost: float) -> bool:
        """
        Update the total cost for a task.

        Args:
            task_id: Task ID
            total_cost: Total cost in USD

        Returns:
            True if updated, False if task not found
        """
        updates = {"total_cost_usd": total_cost}

        if self.local:
            if task_id in local_storage.tasks:
                await local_storage.update_task(task_id, updates)
                logger.info(f"Updated cost for task {task_id}: ${total_cost:.6f}")
                return True
            return False
        else:
            doc_ref = self.db.collection("tasks").document(task_id)
            doc = await doc_ref.get()
            if doc.exists:
                await doc_ref.update(updates)
                logger.info(f"Updated cost for task {task_id}: ${total_cost:.6f}")
                return True
            return False

    async def increment_execution_count(self, task_id: str) -> bool:
        """Increment the execution count for a task."""
        if self.local:
            if task_id in local_storage.tasks:
                current = local_storage.tasks[task_id].get("execution_count", 0)
                await local_storage.update_task(task_id, {"execution_count": current + 1})
                return True
            return False
        else:
            from google.cloud.firestore import Increment
            doc_ref = self.db.collection("tasks").document(task_id)
            doc = await doc_ref.get()
            if doc.exists:
                await doc_ref.update({"execution_count": Increment(1)})
                return True
            return False


# Global tracker instances
execution_tracker = ExecutionTracker()
task_tracker = TaskTracker()
