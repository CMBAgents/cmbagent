import asyncio
import json
import os
import sys
import time
import logging
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from typing import Dict, Any, Optional, List
import uuid
import mimetypes

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

# Add the parent directory to the path to import cmbagent
sys.path.append(str(Path(__file__).parent.parent))

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    import cmbagent
    from cmbagent.utils import get_api_keys_from_env
    from cmbagent.execution import RemoteWebSocketCodeExecutor, RemoteExecutorManager
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

# Global executor manager for routing results to correct executor
executor_manager = RemoteExecutorManager()

# Base work directory - configurable via environment variable
# All user data is stored under: {CMBAGENT_WORK_DIR}/{user_id}/{task_id}
CMBAGENT_WORK_DIR = os.environ.get("CMBAGENT_WORK_DIR", "/tmp/cmbagent_workdir")

# Import auth and execution tracking modules
try:
    from auth import verify_ws_token, User, LocalDevUser, is_local_dev
    from execution_tracker import execution_tracker, task_tracker
    from models import ExecutionStatus, TaskStatus
    REMOTE_EXECUTION_ENABLED = True
    logger.info("Remote execution modules loaded successfully")
except ImportError as e:
    logger.warning(f"Remote execution modules not available: {e}")
    logger.warning("Running in local-only mode")
    REMOTE_EXECUTION_ENABLED = False

    # Mock user for local-only mode
    class User:
        uid: str = "local-user"
        email: str = "local@localhost"
        name: str = "Local User"
        is_local_dev: bool = True

    async def verify_ws_token(token):
        return User()

    def is_local_dev():
        return True

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
        "workDir": CMBAGENT_WORK_DIR
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

class ArxivFilterRequest(BaseModel):
    input_text: str
    work_dir: Optional[str] = None

class ArxivFilterResponse(BaseModel):
    status: str
    result: Dict[str, Any]
    message: str

class EnhanceInputRequest(BaseModel):
    input_text: str
    work_dir: Optional[str] = None
    max_workers: Optional[int] = 2
    max_depth: Optional[int] = 10

class EnhanceInputResponse(BaseModel):
    status: str
    enhanced_text: str
    processing_summary: Dict[str, Any]
    cost_breakdown: Dict[str, Any]
    message: str

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


@app.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    task_id: str = Form(...),
    user_id: str = Form(default="anonymous")
):
    """
    Upload a file (e.g., PDF) for processing.
    Files are stored in: {CMBAGENT_WORK_DIR}/{user_id}/{task_id}/uploads/
    Returns the server-side path for use in OCR or other processing.
    """
    try:
        # Validate file
        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided")

        # Create upload directory
        upload_dir = os.path.join(CMBAGENT_WORK_DIR, user_id, task_id, "uploads")
        os.makedirs(upload_dir, exist_ok=True)

        # Sanitize filename (keep only safe characters)
        safe_filename = "".join(c for c in file.filename if c.isalnum() or c in "._-")
        if not safe_filename:
            safe_filename = f"uploaded_file_{int(time.time())}"

        # Save file
        file_path = os.path.join(upload_dir, safe_filename)

        # Read and write file content
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        file_size = len(content)
        logger.info(f"File uploaded: {file_path} ({file_size} bytes)")

        return {
            "status": "success",
            "filename": safe_filename,
            "original_filename": file.filename,
            "server_path": file_path,
            "size": file_size,
            "upload_dir": upload_dir
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File upload error: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


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
            path = CMBAGENT_WORK_DIR

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

# arXiv Filter API endpoint
@app.post("/api/arxiv/filter", response_model=ArxivFilterResponse)
async def arxiv_filter_endpoint(request: ArxivFilterRequest):
    """
    Extract arXiv URLs from input text and download corresponding PDFs.
    
    Args:
        request: ArxivFilterRequest containing input_text and optional work_dir
        
    Returns:
        ArxivFilterResponse with download results and metadata
    """
    try:
        print(f"Processing arXiv filter request...")
        print(f"Input text length: {len(request.input_text)} characters")
        if request.work_dir:
            print(f"Work directory: {request.work_dir}")
        
        # Use work_dir from request or fall back to cmbagent's default
        work_dir = request.work_dir if request.work_dir else None
        
        # Call the arxiv_filter function
        result = cmbagent.arxiv_filter(
            input_text=request.input_text,
            work_dir=work_dir
        )
        
        # Create success response
        return ArxivFilterResponse(
            status="success",
            result=result,
            message=f"Successfully processed {result['downloads_successful']} downloads out of {len(result['urls_found'])} URLs found"
        )
        
    except Exception as e:
        print(f"Error in arxiv_filter_endpoint: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail=f"Error processing arXiv filter request: {str(e)}"
        )

# Enhance Input API endpoint
@app.post("/api/enhance-input", response_model=EnhanceInputResponse)
async def enhance_input_endpoint(request: EnhanceInputRequest):
    """
    Enhance input text with contextual information from referenced arXiv papers.
    
    Args:
        request: EnhanceInputRequest containing input_text and processing options
        
    Returns:
        EnhanceInputResponse with enhanced text and cost breakdown
    """
    try:
        import os
        import json
        import tempfile
        
        print(f"Processing enhance-input request...")
        print(f"Input text length: {len(request.input_text)} characters")
        print(f"Max workers: {request.max_workers}")
        print(f"Max depth: {request.max_depth}")
        if request.work_dir:
            print(f"Work directory: {request.work_dir}")
        
        # Use work_dir from request or create a temporary one
        work_dir = request.work_dir
        if not work_dir:
            # Create a temporary directory for processing
            work_dir = tempfile.mkdtemp(prefix="enhance_input_")
            print(f"Created temporary work directory: {work_dir}")
        
        # Check if enhanced_input.md already exists to avoid re-processing
        enhanced_input_file = os.path.join(work_dir, "enhanced_input.md")
        if request.work_dir and os.path.exists(enhanced_input_file):
            # Read existing enhanced text if work_dir was provided and file exists
            with open(enhanced_input_file, 'r', encoding='utf-8') as f:
                enhanced_text = f.read()
            print("Using existing enhanced_input.md file")
        else:
            # Call the preprocess_task function
            enhanced_text = cmbagent.preprocess_task(
                text=request.input_text,
                work_dir=work_dir,
                max_workers=request.max_workers,
                max_depth=request.max_depth,
                clear_work_dir=False  # Don't clear when work_dir is provided
            )
        
        # Collect cost information
        cost_breakdown = {}
        processing_summary = {}
        
        # Try to read OCR costs if available
        ocr_cost_file = os.path.join(work_dir, "ocr_cost.json")
        if os.path.exists(ocr_cost_file):
            try:
                with open(ocr_cost_file, 'r') as f:
                    ocr_data = json.load(f)
                    cost_breakdown['ocr'] = {
                        'total_cost': ocr_data.get('total_cost_usd', 0),
                        'pages_processed': ocr_data.get('total_pages_processed', 0),
                        'files_processed': len(ocr_data.get('entries', []))
                    }
            except Exception as e:
                print(f"Warning: Could not read OCR cost file: {e}")
        
        # Try to read summary processing costs
        summaries_dir = os.path.join(work_dir, "summaries")
        if os.path.exists(summaries_dir):
            summary_cost_files = []
            for root, dirs, files in os.walk(summaries_dir):
                for file in files:
                    if file.startswith('cost_report_') and file.endswith('.json'):
                        summary_cost_files.append(os.path.join(root, file))
            
            total_summary_cost = 0
            all_agent_costs = []
            
            for cost_file in summary_cost_files:
                try:
                    with open(cost_file, 'r') as f:
                        cost_data = json.load(f)
                        # Each cost file contains an array of agent cost entries
                        if isinstance(cost_data, list):
                            for entry in cost_data:
                                if isinstance(entry, dict) and entry.get('Agent') != 'Total':
                                    cost_usd = entry.get('Cost ($)', 0)
                                    if isinstance(cost_usd, (int, float)) and not (isinstance(cost_usd, float) and (cost_usd != cost_usd or cost_usd == float('inf'))):  # Check for NaN/inf
                                        total_summary_cost += cost_usd
                                        all_agent_costs.append({
                                            'agent': entry.get('Agent', 'Unknown'),
                                            'cost': cost_usd,
                                            'model': entry.get('Model', 'N/A'),
                                            'prompt_tokens': entry.get('Prompt Tokens', 0),
                                            'completion_tokens': entry.get('Completion Tokens', 0),
                                            'total_tokens': entry.get('Total Tokens', 0)
                                        })
                except Exception as e:
                    print(f"Warning: Could not read summary cost file {cost_file}: {e}")
            
            if all_agent_costs:
                cost_breakdown['summarization'] = {
                    'total_cost': total_summary_cost,
                    'agents': all_agent_costs
                }
        
        # Calculate total cost
        total_cost = 0
        if 'ocr' in cost_breakdown:
            total_cost += cost_breakdown['ocr']['total_cost']
        if 'summarization' in cost_breakdown:
            total_cost += cost_breakdown['summarization']['total_cost']
        
        cost_breakdown['total'] = total_cost
        
        # Create processing summary
        processing_summary = {
            'enhanced_text_length': len(enhanced_text),
            'original_text_length': len(request.input_text),
            'enhancement_added': len(enhanced_text) > len(request.input_text),
            'work_dir': work_dir
        }
        
        # Create success response
        return EnhanceInputResponse(
            status="success",
            enhanced_text=enhanced_text,
            processing_summary=processing_summary,
            cost_breakdown=cost_breakdown,
            message=f"Successfully enhanced input text. Total cost: ${total_cost:.4f}"
        )
        
    except Exception as e:
        print(f"Error in enhance_input_endpoint: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing enhance-input request: {str(e)}"
        )

# Add request/response models for one-shot REST endpoint
class OneShotRequest(BaseModel):
    task: str
    max_rounds: Optional[int] = 50
    max_attempts: Optional[int] = 3
    engineer_model: Optional[str] = "gpt-4o"
    work_dir: Optional[str] = None

class OneShotResponse(BaseModel):
    status: str
    message: str
    work_dir: str
    result: Optional[Dict[str, Any]] = None

@app.post("/api/one-shot")
async def one_shot_sync(request: OneShotRequest):
    """Execute a one-shot task synchronously (for MCP/external integrations)"""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def execute_task():
        """Execute the task in a separate thread"""
        try:
            # Get work directory or create one
            if request.work_dir:
                work_dir = request.work_dir
                if work_dir.startswith("~"):
                    work_dir = os.path.expanduser(work_dir)
            else:
                # Create unique work directory under CMBAGENT_WORK_DIR
                task_id = str(uuid.uuid4())[:8]
                work_dir = os.path.join(CMBAGENT_WORK_DIR, "anonymous", f"one_shot_{task_id}")

            os.makedirs(work_dir, exist_ok=True)

            # Get API keys
            api_keys = get_api_keys_from_env()

            # Execute one_shot (blocking call)
            results = cmbagent.one_shot(
                task=request.task,
                max_rounds=request.max_rounds,
                max_n_attempts=request.max_attempts,
                engineer_model=request.engineer_model,
                agent="engineer",
                work_dir=work_dir,
                api_keys=api_keys,
                clear_work_dir=False
            )

            # Return simple success response with work_dir
            return {
                "status": "success",
                "message": f"Task completed successfully. Output in {work_dir}",
                "work_dir": work_dir,
                "result": {"completed": True}
            }

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"❌ Error in execute_task: {error_details}")
            return {
                "status": "error",
                "message": f"Error executing task: {str(e)}",
                "work_dir": "",
                "result": None
            }

    try:
        # Run the blocking task in a thread pool
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            response_data = await loop.run_in_executor(executor, execute_task)

        # Return the response directly as a dict (FastAPI will serialize it)
        if response_data["status"] == "error":
            raise HTTPException(
                status_code=500,
                detail=response_data["message"]
            )

        return response_data

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"❌ Error in one_shot_sync wrapper: {error_details}")
        raise HTTPException(
            status_code=500,
            detail=f"Error executing one-shot task: {str(e)}"
        )

@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str, token: Optional[str] = Query(None)):
    """
    WebSocket endpoint for task execution.

    Supports two modes:
    1. Local execution (default): Code runs on backend server
    2. Remote execution: Code is sent to frontend for execution

    Message types handled:
    - task_submit: Start a new task
    - execution_ack: Frontend acknowledged execution request
    - execution_result: Frontend completed code execution
    - execution_error: Frontend execution failed
    - files_created: Frontend reports new files
    """
    # Verify authentication (optional in local dev mode)
    user = await verify_ws_token(token)
    if not user and not is_local_dev():
        await websocket.close(code=4001, reason="Authentication required")
        return

    # Use mock user for local dev
    if not user:
        user = LocalDevUser.create() if REMOTE_EXECUTION_ENABLED else User()

    await websocket.accept()
    active_connections[task_id] = websocket
    logger.info(f"WebSocket connected: task={task_id}, user={user.uid}")

    # Track active task execution
    task_execution_future = None
    current_task = None
    current_config = None
    remote_executor = None  # Will be created when task is submitted

    try:
        # Check for pending executions on reconnect (remote execution mode)
        if REMOTE_EXECUTION_ENABLED:
            pending = await execution_tracker.get_pending_for_task(task_id)
            for execution in pending:
                logger.info(f"Re-sending pending execution {execution.execution_id}")
                await websocket.send_json({
                    "type": "execute_code",
                    "execution_id": execution.execution_id,
                    "task_id": task_id,
                    "work_dir": execution.work_dir,
                    "code_blocks": execution.code_blocks,
                    "timeout": execution.timeout,
                })

        # Main message loop
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "task_submit")  # Default for backward compatibility

            if msg_type == "task_submit" or "task" in data:
                # New task submission (backward compatible)
                task = data.get("task", "")
                config = data.get("config", {})

                if not task:
                    await websocket.send_json({
                        "type": "error",
                        "message": "No task provided"
                    })
                    continue

                current_task = task
                current_config = config

                # Send initial status
                await websocket.send_json({
                    "type": "status",
                    "message": "Starting CMBAgent execution..."
                })

                # Check if remote execution is requested
                use_remote_execution = config.get("remoteExecution", True)  # Default to remote

                if use_remote_execution:
                    # Create send callback for the remote executor
                    async def send_to_frontend(message: dict):
                        if websocket.client_state.name == "CONNECTED":
                            await websocket.send_json(message)

                    # Get work directory from config or use default
                    work_dir = config.get("workDir", CMBAGENT_WORK_DIR)
                    if work_dir.startswith("~"):
                        work_dir = os.path.expanduser(work_dir)

                    # Include user_id in the path: {work_dir}/{user_id}/{task_id}
                    user_id = getattr(user, 'uid', None) or "anonymous"
                    task_work_dir = os.path.join(work_dir, user_id, task_id)

                    # Create remote executor
                    remote_executor = RemoteWebSocketCodeExecutor(
                        send_callback=send_to_frontend,
                        work_dir=task_work_dir,
                        timeout=config.get("timeout", 86400),  # 24 hours default
                        task_id=task_id,
                    )

                    # Set the event loop for async operations
                    remote_executor.set_event_loop(asyncio.get_event_loop())

                    # Register the executor
                    executor_manager.register(task_id, remote_executor)
                    logger.info(f"Created remote executor for task {task_id}")
                else:
                    remote_executor = None
                    logger.info(f"Using local execution for task {task_id}")

                # Execute the task (runs in background)
                task_execution_future = asyncio.create_task(
                    execute_cmbagent_task(websocket, task_id, task, config, user, remote_executor)
                )

            elif msg_type == "execution_ack":
                # Frontend acknowledged execution request
                execution_id = data.get("execution_id")
                if execution_id and REMOTE_EXECUTION_ENABLED:
                    await execution_tracker.mark_acked(execution_id)
                    logger.debug(f"Execution {execution_id} acknowledged")

            elif msg_type == "execution_result":
                # Frontend completed code execution
                execution_id = data.get("execution_id")
                result = data.get("result", {})

                if execution_id:
                    # Route result to the waiting executor
                    routed = executor_manager.route_result(task_id, execution_id, result)
                    if routed:
                        logger.info(f"Execution {execution_id} completed: exit_code={result.get('exit_code')}")
                    else:
                        logger.warning(f"Could not route result for execution {execution_id}")

                    # Also track in Firestore if enabled
                    if REMOTE_EXECUTION_ENABLED:
                        await execution_tracker.mark_completed(execution_id, result)

            elif msg_type == "execution_error":
                # Frontend execution failed
                execution_id = data.get("execution_id")
                error = data.get("error", "Unknown error")

                if execution_id:
                    # Route error to the waiting executor
                    routed = executor_manager.route_error(task_id, execution_id, error)
                    if routed:
                        logger.warning(f"Execution {execution_id} failed: {error}")
                    else:
                        logger.warning(f"Could not route error for execution {execution_id}")

                    # Also track in Firestore if enabled
                    if REMOTE_EXECUTION_ENABLED:
                        await execution_tracker.mark_failed(execution_id, error)

            elif msg_type == "files_created":
                # Frontend reports new files created during execution
                execution_id = data.get("execution_id")
                files = data.get("files", [])

                if files and REMOTE_EXECUTION_ENABLED:
                    await task_tracker.register_files(task_id, files, source="execution")
                    logger.debug(f"Registered {len(files)} files for task {task_id}")

            elif msg_type == "install_complete":
                # Frontend completed package installation
                packages = data.get("packages", [])
                success = data.get("success", False)
                failed = data.get("failed", [])

                if success:
                    logger.info(f"Packages installed: {packages}")
                else:
                    logger.warning(f"Some packages failed to install: {failed}")

            else:
                logger.warning(f"Unknown message type: {msg_type}")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: task={task_id}, user={user.uid}")
    except Exception as e:
        logger.error(f"Error in WebSocket endpoint: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Execution error: {str(e)}"
            })
        except:
            pass
    finally:
        # Cancel any running task execution
        if task_execution_future and not task_execution_future.done():
            task_execution_future.cancel()

        # Unregister the remote executor
        executor_manager.unregister(task_id)

        if task_id in active_connections:
            del active_connections[task_id]


async def _calculate_total_cost(work_dir: str, mode: str = None) -> float:
    """
    Calculate total cost from cost report files.

    For planning modes, aggregates costs from planning/ and control/ subdirectories.

    Args:
        work_dir: Backend work directory path
        mode: Task execution mode

    Returns:
        Total cost in USD
    """
    import json
    import glob

    total_cost = 0.0
    planning_modes = ['idea-generation', 'deep_research', 'planning_and_control']

    # Determine where to look for cost reports
    cost_dirs = []
    if mode in planning_modes:
        # Look in planning/cost and control/cost
        cost_dirs.append(os.path.join(work_dir, 'planning', 'cost'))
        cost_dirs.append(os.path.join(work_dir, 'control', 'cost'))
    else:
        # Standard one-shot mode: cost/ at top level
        cost_dirs.append(os.path.join(work_dir, 'cost'))

    for cost_dir in cost_dirs:
        if not os.path.exists(cost_dir):
            continue

        # Find all cost report JSON files
        pattern = os.path.join(cost_dir, 'cost_report_*.json')
        for cost_file in glob.glob(pattern):
            try:
                with open(cost_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Handle NaN values in JSON
                    content = content.replace(': NaN', ': null')
                    cost_data = json.loads(content)

                # Sum up costs (skip 'Total' rows to avoid double counting)
                for entry in cost_data:
                    if entry.get('Agent') != 'Total':
                        cost_value = entry.get('Cost ($)', 0)
                        if cost_value and isinstance(cost_value, (int, float)):
                            total_cost += cost_value

            except Exception as e:
                logger.warning(f"Failed to parse cost file {cost_file}: {e}")

    return total_cost


async def sync_backend_files_to_frontend(
    websocket: WebSocket,
    work_dir: str,
    mode: str = None
) -> tuple[int, list[dict]]:
    """
    Sync files from backend work directory to frontend.

    Sends files from chats/, cost/, time/ directories to the frontend
    so users have all task outputs locally.

    For planning modes (idea-generation, deep_research, planning_and_control),
    also syncs from planning/ and control/ subdirectories.

    Args:
        websocket: WebSocket connection
        work_dir: Backend work directory path
        mode: Task execution mode (affects which directories are synced)

    Returns:
        Tuple of (files_synced_count, file_registry_entries)
    """
    import base64

    dirs_to_sync = ['chats', 'cost', 'time', 'context', 'codebase', 'data']
    files_synced = 0
    file_registry_entries = []  # Track for Firestore registration

    # Determine search roots based on mode
    # For planning modes, also search in planning/ and control/ subdirectories
    search_roots = [work_dir]
    planning_modes = ['idea-generation', 'deep_research', 'planning_and_control']

    if mode in planning_modes:
        for subdir in ['planning', 'control']:
            subdir_path = os.path.join(work_dir, subdir)
            if os.path.exists(subdir_path):
                search_roots.append(subdir_path)
                logger.info(f"Adding {subdir}/ to sync search roots for mode={mode}")

    # Helper function to sync a single file
    async def sync_file(file_path: str, relative_path: str) -> bool:
        nonlocal files_synced
        try:
            file_size = os.path.getsize(file_path)

            # Try reading as text first
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                await websocket.send_json({
                    "type": "write_file",
                    "path": relative_path,
                    "content": content,
                    "work_dir": work_dir,
                })
            except UnicodeDecodeError:
                # Binary file - encode as base64
                with open(file_path, 'rb') as f:
                    content = base64.b64encode(f.read()).decode('ascii')

                await websocket.send_json({
                    "type": "write_file",
                    "path": relative_path,
                    "content": content,
                    "work_dir": work_dir,
                    "encoding": "base64",
                })

            files_synced += 1
            file_registry_entries.append({
                "path": relative_path,
                "size": file_size,
                "source": "backend",
            })
            return True

        except Exception as e:
            logger.warning(f"Failed to sync file {relative_path}: {e}")
            return False

    # Sync standard directories (chats/, cost/, time/) from all search roots
    for search_root in search_roots:
        for subdir in dirs_to_sync:
            dir_path = os.path.join(search_root, subdir)
            if not os.path.exists(dir_path):
                continue

            for root, _, files in os.walk(dir_path):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    # Keep path relative to original work_dir for correct frontend structure
                    relative_path = os.path.relpath(file_path, work_dir)
                    await sync_file(file_path, relative_path)

    # Sync special files for planning modes
    if mode in planning_modes:
        special_files = [
            'planning/final_plan.json',
            'planning/final_plan.md',
        ]
        for rel_path in special_files:
            file_path = os.path.join(work_dir, rel_path)
            if os.path.exists(file_path):
                logger.info(f"Syncing special file: {rel_path}")
                await sync_file(file_path, rel_path)

    if files_synced > 0:
        logger.info(f"Synced {files_synced} files from backend to frontend (mode={mode})")
        await websocket.send_json({
            "type": "output",
            "data": f"📁 Synced {files_synced} files to local directory"
        })

    return files_synced, file_registry_entries


async def execute_cmbagent_task(websocket: WebSocket, task_id: str, task: str, config: Dict[str, Any], user: Optional[Any] = None, custom_executor: Optional[Any] = None):
    """Execute CMBAgent task with real-time output streaming.

    Args:
        websocket: WebSocket connection
        task_id: Unique task identifier
        task: Task description
        config: Task configuration
        user: Authenticated user (optional, for tracking)
        custom_executor: Remote code executor (optional, for frontend execution)
    """

    # Use the global CMBAGENT_WORK_DIR constant
    base_work_dir = CMBAGENT_WORK_DIR

    # Get user ID for organizing data by user
    # Falls back to "anonymous" for unauthenticated users
    user_id = getattr(user, 'uid', None) if user else None
    if not user_id:
        user_id = "anonymous"

    # Work directory structure: {base}/{user_id}/{task_id}
    # e.g., /rds/rds-ai-scientist/users-data/M6cZbIaFogbCZYccD29h2MlArex1/task_123
    work_dir = os.path.join(base_work_dir, user_id)
    task_work_dir = os.path.join(work_dir, task_id)

    # Create directories
    os.makedirs(task_work_dir, exist_ok=True)

    logger.info(f"Task {task_id} for user {user_id} - work dir: {task_work_dir}")

    # Create task record in Firestore
    try:
        await task_tracker.create_task(
            task_id=task_id,
            user_id=user_id,
            prompt=task,
            config=config,
            mode=config.get("mode", "one-shot"),
        )
        logger.info(f"Task {task_id} created in Firestore")
    except Exception as e:
        logger.warning(f"Failed to create task in Firestore: {e}")

    # Set up environment variables to disable display and enable debug if needed
    os.environ["CMBAGENT_DEBUG"] = "false"
    os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"
    
    try:
        # Send status update
        await websocket.send_json({
            "type": "status",
            "message": "Initializing CMBAgent..."
        })

        # Update task status to running in Firestore
        try:
            from models import TaskStatus
            await task_tracker.update_task_status(task_id, TaskStatus.RUNNING)
        except Exception as e:
            logger.warning(f"Failed to update task status to running: {e}")

        # Get API keys from environment
        api_keys = get_api_keys_from_env()
        
        # Map frontend config to CMBAgent parameters
        mode = config.get("mode", "one-shot")
        engineer_model = config.get("model", "gpt-4o")
        max_rounds = config.get("maxRounds", 50)
        max_attempts = config.get("maxAttempts", 6)
        agent = config.get("agent", "engineer")
        default_formatter_model = config.get("defaultFormatterModel", "o3-mini-2025-01-31")
        default_llm_model = config.get("defaultModel", "gpt-4.1-2025-04-14")
        
        # Debug: Log the received config
        print(f"DEBUG: Received config: {config}")
        print(f"DEBUG: defaultModel = {default_llm_model}")
        print(f"DEBUG: defaultFormatterModel = {default_formatter_model}")

        
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
        
        # OCR specific parameters
        save_markdown = config.get("saveMarkdown", True)
        save_json = config.get("saveJson", True)
        save_text = config.get("saveText", False)
        max_workers = config.get("maxWorkers", 4)
        ocr_output_dir = config.get("ocrOutputDir", None)
        
        await websocket.send_json({
            "type": "output",
            "data": f"🚀 Starting CMBAgent in {mode.replace('-', ' ').title()} mode"
        })
        
        await websocket.send_json({
            "type": "output",
            "data": f"🚀 Default LLM Model: {default_llm_model}"
        })

        await websocket.send_json({
            "type": "output",
            "data": f"🚀 Default Formatter Model: {default_formatter_model}"
        })



        await websocket.send_json({
            "type": "output", 
            "data": f"📋 Task: {task}"
        })
        
        if mode == "deep_research":
            await websocket.send_json({
                "type": "output",
                "data": f"⚙️ Configuration: Planner={planner_model}, Engineer={engineer_model}, Researcher={researcher_model}, Plan Reviewer={plan_reviewer_model}"
            })
        elif mode == "idea-generation":
            await websocket.send_json({
                "type": "output",
                "data": f"⚙️ Configuration: Idea Maker={idea_maker_model}, Idea Hater={idea_hater_model}, Planner={planner_model}, Plan Reviewer={plan_reviewer_model}"
            })
        elif mode == "ocr":
            await websocket.send_json({
                "type": "output",
                "data": f"⚙️ Configuration: Save Markdown={save_markdown}, Save JSON={save_json}, Save Text={save_text}, Max Workers={max_workers}"
            })
        elif mode == "arxiv":
            await websocket.send_json({
                "type": "output",
                "data": f"⚙️ Configuration: arXiv Filter mode - Scanning text for arXiv URLs and downloading papers"
            })
        elif mode == "enhance-input":
            max_depth = config.get("maxDepth", 10)
            await websocket.send_json({
                "type": "output",
                "data": f"⚙️ Configuration: Enhance Input mode - Max Workers={max_workers}, Max Depth={max_depth}"
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
                if mode == "deep_research":
                    results = cmbagent.deep_research(
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
                        clear_work_dir=False,
                        default_formatter_model=default_formatter_model,
                        default_llm_model=default_llm_model,
                        custom_executor=custom_executor,
                    )
                elif mode == "idea-generation":
                    # Idea Generation mode - uses deep_research with idea agents
                    results = cmbagent.deep_research(
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
                        clear_work_dir=False,
                        default_formatter_model=default_formatter_model,
                        default_llm_model=default_llm_model
                    )
                elif mode == "ocr":
                    # OCR mode - process PDFs with Mistral OCR
                    import os
                    
                    # task should be the path to PDF file or folder
                    pdf_path = task.strip()
                    
                    # Expand user path if needed
                    if pdf_path.startswith("~"):
                        pdf_path = os.path.expanduser(pdf_path)
                    
                    # Check if path exists
                    if not os.path.exists(pdf_path):
                        raise ValueError(f"Path does not exist: {pdf_path}")
                    
                    # Use OCR output directory if specified, otherwise use default logic
                    output_dir = ocr_output_dir if ocr_output_dir and ocr_output_dir.strip() else None
                    
                    if os.path.isfile(pdf_path):
                        # Single PDF file
                        results = cmbagent.process_single_pdf(
                            pdf_path=pdf_path,
                            save_markdown=save_markdown,
                            save_json=save_json,
                            save_text=save_text,
                            output_dir=output_dir,
                            work_dir=task_work_dir
                        )
                    elif os.path.isdir(pdf_path):
                        # Folder containing PDFs
                        results = cmbagent.process_folder(
                            folder_path=pdf_path,
                            save_markdown=save_markdown,
                            save_json=save_json,
                            save_text=save_text,
                            output_dir=output_dir,
                            max_workers=max_workers,
                            work_dir=task_work_dir
                        )
                    else:
                        raise ValueError(f"Path is neither a file nor a directory: {pdf_path}")
                elif mode == "arxiv":
                    # arXiv Filter mode - scan text for arXiv URLs and download papers
                    results = cmbagent.arxiv_filter(
                        input_text=task,
                        work_dir=task_work_dir
                    )
                elif mode == "enhance-input":
                    # Enhance Input mode - enhance input text with contextual information
                    max_depth = config.get("maxDepth", 10)
                    results = cmbagent.preprocess_task(
                        text=task,
                        work_dir=task_work_dir,
                        max_workers=max_workers,
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
                        clear_work_dir=False,
                        default_formatter_model=default_formatter_model,
                        default_llm_model=default_llm_model,
                        custom_executor=custom_executor
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

        # Update task status in Firestore
        try:
            from models import TaskStatus
            await task_tracker.update_task_status(task_id, TaskStatus.COMPLETED)
            logger.info(f"Task {task_id} marked as completed in Firestore")
        except Exception as e:
            logger.warning(f"Failed to update task status in Firestore: {e}")

        # Sync backend files (chats, cost, time) to frontend
        files_synced, file_entries = await sync_backend_files_to_frontend(
            websocket, task_work_dir, mode=mode
        )

        # Register synced files in Firestore
        if file_entries:
            try:
                await task_tracker.register_files(task_id, file_entries, source="backend")
                logger.info(f"Registered {len(file_entries)} files in Firestore for task {task_id}")
            except Exception as e:
                logger.warning(f"Failed to register files in Firestore: {e}")

        # Calculate and store total cost from cost reports
        try:
            total_cost = await _calculate_total_cost(task_work_dir, mode)
            if total_cost > 0:
                await task_tracker.update_task_cost(task_id, total_cost)
                logger.info(f"Updated total cost for task {task_id}: ${total_cost:.6f}")
        except Exception as e:
            logger.warning(f"Failed to calculate/store total cost: {e}")

        # Send final results
        await websocket.send_json({
            "type": "result",
            "data": {
                "execution_time": execution_time,
                "chat_history": getattr(results, 'chat_history', []) if hasattr(results, 'chat_history') else [],
                "final_context": getattr(results, 'final_context', {}) if hasattr(results, 'final_context') else {},
                "work_dir": task_work_dir,
                "base_work_dir": work_dir,
                "mode": mode  # Include mode so UI knows how to display results
            }
        })

        await websocket.send_json({
            "type": "complete",
            "message": "Task execution completed successfully"
        })
        
    except Exception as e:
        error_msg = f"Error executing CMBAgent task: {str(e)}"
        print(error_msg)

        # Update task status to failed in Firestore
        try:
            from models import TaskStatus
            await task_tracker.update_task_status(task_id, TaskStatus.FAILED, error=str(e))
            logger.warning(f"Task {task_id} marked as failed in Firestore")
        except Exception as fe:
            logger.warning(f"Failed to update task status in Firestore: {fe}")

        await websocket.send_json({
            "type": "error",
            "message": error_msg
        })


