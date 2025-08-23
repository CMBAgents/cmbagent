#!/usr/bin/env python3
"""
Simple script to run the CMBAgent backend server
"""

import uvicorn
import sys
from pathlib import Path

# Add the parent directory to the path to import cmbagent
sys.path.append(str(Path(__file__).parent.parent))

if __name__ == "__main__":
    print("🚀 Starting CMBAgent Backend Server...")
    print("📡 Server will be available at: http://localhost:8000")
    print("🔌 WebSocket endpoint: ws://localhost:8000/ws/{task_id}")
    print("📖 API docs: http://localhost:8000/docs")
    print("\n" + "="*50)
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
