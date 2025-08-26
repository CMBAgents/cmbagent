import asyncio
import json
import os
import sys
import time
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from typing import Dict, Any, Optional, List
import uuid
import mimetypes

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

# Add the parent directory to the path to import cmbagent
sys.path.append(str(Path(__file__).parent.parent))

try:
    import cmbagent
    from cmbagent.utils import get_api_keys_from_env
    from credentials import (
        test_all_credentials, 
        test_openai_credentials, 
        test_anthropic_credentials, 
        test_vertex_credentials,
        store_credentials_in_env,
        CredentialStorage,
        CredentialTest
    )
except ImportError as e:
    print(f"Error importing cmbagent: {e}")
    print("Make sure cmbagent is installed and accessible")
    sys.exit(1)

app = FastAPI(title="CMBAgent API", version="1.0.0")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001", 
        "http://localhost:3002",
        "http://localhost:3003",
        "http://localhost:3004"
    ],  # Next.js dev server on various ports
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store active WebSocket connections
active_connections: Dict[str, WebSocket] = {}

class TaskRequest(BaseModel):
    task: str
    config: Dict[str, Any] = {
        "model": "gpt-4o",
        "maxRounds": 25,
        "maxAttempts": 6,
        "agent": "engineer",
        "workDir": "~/Desktop/cmbdir"
    }

class TaskResponse(BaseModel):
    task_id: str
    status: str
    message: str

class FileItem(BaseModel):
    name: str
    path: str
    type: str  # 'file' or 'directory'
    size: Optional[int] = None
    modified: Optional[float] = None
    mime_type: Optional[str] = None

class DirectoryListing(BaseModel):
    path: str
    items: List[FileItem]
    parent: Optional[str] = None

class StreamCapture:
    """Capture stdout/stderr and send to WebSocket"""
    
    def __init__(self, websocket: WebSocket, task_id: str):
        self.websocket = websocket
        self.task_id = task_id
        self.buffer = StringIO()
        
    async def write(self, text: str):
        """Write text to buffer and send to WebSocket"""
        if text.strip():  # Only send non-empty lines
            try:
                await self.websocket.send_json({
                    "type": "output",
                    "task_id": self.task_id,
                    "data": text.strip()
                })
            except Exception as e:
                print(f"Error sending to WebSocket: {e}")
        
        # Also write to buffer for later retrieval
        self.buffer.write(text)
        return len(text)
    
    def flush(self):
        """Flush the buffer"""
        pass
    
    def getvalue(self):
        """Get the complete output"""
        return self.buffer.getvalue()

@app.get("/")
async def root():
    return {"message": "CMBAgent API is running"}

@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time()}

@app.post("/api/task/submit", response_model=TaskResponse)
async def submit_task(request: TaskRequest):
    """Submit a task for execution"""
    task_id = str(uuid.uuid4())

    return TaskResponse(
        task_id=task_id,
        status="submitted",
        message="Task submitted successfully. Connect to WebSocket for real-time updates."
    )

@app.get("/api/files/list")
async def list_directory(path: str = ""):
    """List files and directories in the specified path"""
    try:
        # Expand user path and resolve
        if path.startswith("~"):
            path = os.path.expanduser(path)

        if not path:
            path = os.path.expanduser("~/Desktop/cmbdir")

        path = os.path.abspath(path)

        # Security check - ensure path is within allowed directories
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="Directory not found")

        if not os.path.isdir(path):
            raise HTTPException(status_code=400, detail="Path is not a directory")

        items = []
        try:
            for item_name in sorted(os.listdir(path)):
                item_path = os.path.join(path, item_name)

                # Skip hidden files
                if item_name.startswith('.'):
                    continue

                stat_info = os.stat(item_path)
                is_dir = os.path.isdir(item_path)

                file_item = FileItem(
                    name=item_name,
                    path=item_path,
                    type="directory" if is_dir else "file",
                    size=None if is_dir else stat_info.st_size,
                    modified=stat_info.st_mtime,
                    mime_type=None if is_dir else mimetypes.guess_type(item_path)[0]
                )
                items.append(file_item)
        except PermissionError:
            raise HTTPException(status_code=403, detail="Permission denied")

        # Get parent directory
        parent = os.path.dirname(path) if path != "/" else None

        return DirectoryListing(
            path=path,
            items=items,
            parent=parent
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/files/content")
async def get_file_content(path: str):
    """Get the content of a file"""
    try:
        # Expand user path and resolve
        if path.startswith("~"):
            path = os.path.expanduser(path)

        path = os.path.abspath(path)

        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="File not found")

        if not os.path.isfile(path):
            raise HTTPException(status_code=400, detail="Path is not a file")

        # Check file size (limit to 10MB for safety)
        file_size = os.path.getsize(path)
        if file_size > 10 * 1024 * 1024:  # 10MB
            raise HTTPException(status_code=413, detail="File too large")

        # Try to read as text first
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            return {
                "path": path,
                "content": content,
                "type": "text",
                "size": file_size,
                "mime_type": mimetypes.guess_type(path)[0]
            }
        except UnicodeDecodeError:
            # If it's not text, return file info only
            return {
                "path": path,
                "content": None,
                "type": "binary",
                "size": file_size,
                "mime_type": mimetypes.guess_type(path)[0],
                "message": "Binary file - content not displayed"
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/files/clear-directory")
async def clear_directory(path: str):
    """Clear all contents of a directory"""
    try:
        # Expand user path
        if path.startswith("~"):
            path = os.path.expanduser(path)

        abs_path = os.path.abspath(path)

        # Security check - ensure path exists and is a directory
        if not os.path.exists(abs_path):
            raise HTTPException(status_code=404, detail="Directory not found")

        if not os.path.isdir(abs_path):
            raise HTTPException(status_code=400, detail="Path is not a directory")

        # Count items before deletion
        items_deleted = 0

        # Remove all contents
        import shutil
        for item in os.listdir(abs_path):
            item_path = os.path.join(abs_path, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
            items_deleted += 1

        return {
            "message": f"Successfully cleared directory: {path}",
            "items_deleted": items_deleted,
            "path": abs_path
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error clearing directory: {str(e)}")

@app.get("/api/files/images")
async def get_images(work_dir: str):
    """Get all image files from the working directory"""
    try:
        # Expand user path
        if work_dir.startswith("~"):
            work_dir = os.path.expanduser(work_dir)

        abs_path = os.path.abspath(work_dir)

        # Check if directory exists
        if not os.path.exists(abs_path) or not os.path.isdir(abs_path):
            return {"images": [], "message": "Working directory not found"}

        # Common image extensions
        image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg', '.webp', '.tiff', '.tif'}

        images = []

        # Recursively search for image files
        for root, dirs, files in os.walk(abs_path):
            for file in files:
                file_path = os.path.join(root, file)
                file_ext = os.path.splitext(file)[1].lower()

                if file_ext in image_extensions:
                    # Get relative path from work_dir
                    rel_path = os.path.relpath(file_path, abs_path)

                    # Get file stats
                    stat = os.stat(file_path)

                    images.append({
                        "name": file,
                        "path": file_path,
                        "relative_path": rel_path,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "extension": file_ext,
                        "directory": os.path.dirname(rel_path) if os.path.dirname(rel_path) else "root"
                    })

        # Sort by modification time (newest first)
        images.sort(key=lambda x: x['modified'], reverse=True)

        return {
            "work_dir": work_dir,
            "images": images,
            "count": len(images)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error scanning for images: {str(e)}")

@app.get("/api/files/serve-image")
async def serve_image(path: str):
    """Serve an image file"""
    try:
        # Security check - ensure path exists and is a file
        abs_path = os.path.abspath(path)

        if not os.path.exists(abs_path) or not os.path.isfile(abs_path):
            raise HTTPException(status_code=404, detail="Image file not found")

        # Check if it's an image file
        image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg', '.webp', '.tiff', '.tif'}
        file_ext = os.path.splitext(abs_path)[1].lower()

        if file_ext not in image_extensions:
            raise HTTPException(status_code=400, detail="File is not an image")

        # Determine MIME type
        mime_types = {
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.svg': 'image/svg+xml',
            '.webp': 'image/webp',
            '.tiff': 'image/tiff',
            '.tif': 'image/tiff'
        }

        mime_type = mime_types.get(file_ext, 'application/octet-stream')

        # Return the file
        return FileResponse(abs_path, media_type=mime_type)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error serving image: {str(e)}")

# API Credentials endpoints
@app.get("/api/credentials/test-all")
async def test_all_api_credentials():
    """Test all configured API credentials"""
    try:
        results = await test_all_credentials()
        return {
            "status": "success",
            "results": results,
            "timestamp": time.time()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error testing credentials: {str(e)}")

@app.post("/api/credentials/test")
async def test_specific_credentials(credentials: CredentialStorage):
    """Test specific credentials provided by the user"""
    try:
        results = {}
        
        if credentials.openai_key:
            results['openai'] = await test_openai_credentials(credentials.openai_key)
        
        if credentials.anthropic_key:
            results['anthropic'] = await test_anthropic_credentials(credentials.anthropic_key)
        
        if credentials.vertex_json:
            results['vertex'] = await test_vertex_credentials(credentials.vertex_json)
        
        return {
            "status": "success",
            "results": results,
            "timestamp": time.time()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error testing credentials: {str(e)}")

@app.post("/api/credentials/store")
async def store_api_credentials(credentials: CredentialStorage):
    """Store API credentials in environment variables (session only)"""
    try:
        updates = store_credentials_in_env(credentials)
        
        # Test the newly stored credentials
        test_results = await test_all_credentials()
        
        return {
            "status": "success",
            "message": "Credentials stored successfully",
            "updates": updates,
            "test_results": test_results,
            "timestamp": time.time()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error storing credentials: {str(e)}")

@app.get("/api/credentials/status")
async def get_credentials_status():
    """Get current status of all API credentials"""
    try:
        results = await test_all_credentials()
        
        # Create summary status
        summary = {
            "total": len(results),
            "valid": sum(1 for r in results.values() if r.status == "valid"),
            "invalid": sum(1 for r in results.values() if r.status == "invalid"),
            "not_configured": sum(1 for r in results.values() if r.status == "not_configured"),
            "errors": sum(1 for r in results.values() if r.status == "error")
        }
        
        return {
            "status": "success",
            "summary": summary,
            "results": results,
            "timestamp": time.time()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting credentials status: {str(e)}")

@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    await websocket.accept()
    active_connections[task_id] = websocket
    
    try:
        # Wait for task data
        data = await websocket.receive_json()
        task = data.get("task", "")
        config = data.get("config", {})
        
        if not task:
            await websocket.send_json({
                "type": "error",
                "message": "No task provided"
            })
            return
        
        # Send initial status
        await websocket.send_json({
            "type": "status",
            "message": "Starting CMBAgent execution..."
        })
        
        # Execute the task
        await execute_cmbagent_task(websocket, task_id, task, config)
        
    except WebSocketDisconnect:
        print(f"WebSocket disconnected for task {task_id}")
    except Exception as e:
        print(f"Error in WebSocket endpoint: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Execution error: {str(e)}"
            })
        except:
            pass
    finally:
        if task_id in active_connections:
            del active_connections[task_id]

async def execute_cmbagent_task(websocket: WebSocket, task_id: str, task: str, config: Dict[str, Any]):
    """Execute CMBAgent task with real-time output streaming"""

    # Get work directory from config or use default
    work_dir = config.get("workDir", "~/Desktop/cmbdir")
    if work_dir.startswith("~"):
        work_dir = os.path.expanduser(work_dir)

    # Create a subdirectory for this specific task
    task_work_dir = os.path.join(work_dir, task_id)
    os.makedirs(task_work_dir, exist_ok=True)
    
    # Set up environment variables to disable display and enable debug if needed
    os.environ["CMBAGENT_DEBUG"] = "false"
    os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"
    
    try:
        # Send status update
        await websocket.send_json({
            "type": "status",
            "message": "Initializing CMBAgent..."
        })
        
        # Get API keys from environment
        api_keys = get_api_keys_from_env()
        
        # Map frontend config to CMBAgent parameters
        mode = config.get("mode", "one-shot")
        engineer_model = config.get("model", "gpt-4o")
        max_rounds = config.get("maxRounds", 25)
        max_attempts = config.get("maxAttempts", 6)
        agent = config.get("agent", "engineer")
        
        # Planning & Control specific parameters
        planner_model = config.get("plannerModel", "gpt-4.1-2025-04-14")
        plan_reviewer_model = config.get("planReviewerModel", "o3-mini-2025-01-31")
        researcher_model = config.get("researcherModel", "gpt-4.1-2025-04-14")
        max_plan_steps = config.get("maxPlanSteps", 6 if mode == "idea-generation" else 2)
        n_plan_reviews = config.get("nPlanReviews", 1)
        plan_instructions = config.get("planInstructions", "")
        
        # Idea Generation specific parameters
        idea_maker_model = config.get("ideaMakerModel", "gpt-4.1-2025-04-14")
        idea_hater_model = config.get("ideaHaterModel", "o3-mini-2025-01-31")
        
        await websocket.send_json({
            "type": "output",
            "data": f"🚀 Starting CMBAgent in {mode.replace('-', ' ').title()} mode"
        })
        
        await websocket.send_json({
            "type": "output", 
            "data": f"📋 Task: {task}"
        })
        
        if mode == "planning-control":
            await websocket.send_json({
                "type": "output",
                "data": f"⚙️ Configuration: Planner={planner_model}, Engineer={engineer_model}, Researcher={researcher_model}, Plan Reviewer={plan_reviewer_model}"
            })
        elif mode == "idea-generation":
            await websocket.send_json({
                "type": "output",
                "data": f"⚙️ Configuration: Idea Maker={idea_maker_model}, Idea Hater={idea_hater_model}, Planner={planner_model}, Plan Reviewer={plan_reviewer_model}"
            })
        else:
            await websocket.send_json({
                "type": "output",
                "data": f"⚙️ Configuration: Agent={agent}, Model={engineer_model}, MaxRounds={max_rounds}, MaxAttempts={max_attempts}"
            })
        
        # Create a custom stdout/stderr capture
        stream_capture = StreamCapture(websocket, task_id)
        
        start_time = time.time()
        
        # Execute the one_shot function in a separate thread to avoid blocking
        loop = asyncio.get_event_loop()
        
        def run_cmbagent():
            # Redirect stdout and stderr to our custom capture
            original_stdout = sys.stdout
            original_stderr = sys.stderr
            
            try:
                # Create a custom print function that sends to WebSocket
                def custom_print(*args, **kwargs):
                    output = " ".join(str(arg) for arg in args)
                    # Run the async write in the event loop
                    asyncio.run_coroutine_threadsafe(
                        stream_capture.write(output + "\n"), loop
                    )
                
                # Replace built-in print
                import builtins
                original_print = builtins.print
                builtins.print = custom_print
                
                # Execute CMBAgent based on mode
                if mode == "planning-control":
                    results = cmbagent.planning_and_control_context_carryover(
                        task=task,
                        max_rounds_control=max_rounds,
                        max_n_attempts=max_attempts,
                        max_plan_steps=max_plan_steps,
                        n_plan_reviews=n_plan_reviews,
                        engineer_model=engineer_model,
                        researcher_model=researcher_model,
                        planner_model=planner_model,
                        plan_reviewer_model=plan_reviewer_model,
                        plan_instructions=plan_instructions if plan_instructions.strip() else None,
                        work_dir=task_work_dir,
                        api_keys=api_keys,
                        clear_work_dir=False
                    )
                elif mode == "idea-generation":
                    # Idea Generation mode - uses planning_and_control_context_carryover with idea agents
                    results = cmbagent.planning_and_control_context_carryover(
                        task=task,
                        max_rounds_control=max_rounds,
                        max_n_attempts=max_attempts,
                        max_plan_steps=max_plan_steps,
                        n_plan_reviews=n_plan_reviews,
                        idea_maker_model=idea_maker_model,
                        idea_hater_model=idea_hater_model,
                        planner_model=planner_model,
                        plan_reviewer_model=plan_reviewer_model,
                        plan_instructions=plan_instructions if plan_instructions.strip() else None,
                        work_dir=task_work_dir,
                        api_keys=api_keys,
                        clear_work_dir=False
                    )
                else:
                    # One Shot mode
                    results = cmbagent.one_shot(
                        task=task,
                        max_rounds=max_rounds,
                        max_n_attempts=max_attempts,
                        engineer_model=engineer_model,
                        agent=agent,
                        work_dir=task_work_dir,
                        api_keys=api_keys,
                        clear_work_dir=False
                    )
                
                return results
                
            finally:
                # Restore original print and streams
                builtins.print = original_print
                sys.stdout = original_stdout
                sys.stderr = original_stderr
        
        # Run CMBAgent in executor to avoid blocking the event loop
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_cmbagent)
            
            # Wait for completion with periodic status updates
            while not future.done():
                await asyncio.sleep(1)
                await websocket.send_json({
                    "type": "heartbeat",
                    "timestamp": time.time()
                })
            
            # Get the results
            results = future.result()
        
        execution_time = time.time() - start_time
        
        # Send completion status
        await websocket.send_json({
            "type": "output",
            "data": f"✅ Task completed in {execution_time:.2f} seconds"
        })
        
        # Send final results
        await websocket.send_json({
            "type": "result",
            "data": {
                "execution_time": execution_time,
                "chat_history": getattr(results, 'chat_history', []) if hasattr(results, 'chat_history') else [],
                "final_context": getattr(results, 'final_context', {}) if hasattr(results, 'final_context') else {},
                "work_dir": task_work_dir,
                "base_work_dir": work_dir
            }
        })
        
        await websocket.send_json({
            "type": "complete",
            "message": "Task execution completed successfully"
        })
        
    except Exception as e:
        error_msg = f"Error executing CMBAgent task: {str(e)}"
        print(error_msg)
        
        await websocket.send_json({
            "type": "error",
            "message": error_msg
        })

