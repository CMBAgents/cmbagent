import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

def run_next_gui():
    """Run the Next.js GUI with FastAPI backend"""
    import signal
    import time
    
    # Get the installed file path to cmbagent package
    cmbagent_spec = find_spec("cmbagent")
    if cmbagent_spec is None or cmbagent_spec.origin is None:
        print("❌ Could not locate cmbagent package")
        sys.exit(1)
    
    # Get the package root directory
    package_root = Path(cmbagent_spec.origin).parent.parent
    backend_path = package_root / "backend"
    frontend_path = package_root / "cmbagent-ui"
    
    # Check if this is a development installation or pip installation
    is_editable_install = (package_root / ".git").exists() or "site-packages" not in str(package_root)
    
    # Check if directories exist
    if not backend_path.exists() or not frontend_path.exists():
        print("❌ Next.js UI components not found!")
        print("")
        if is_editable_install:
            print("It looks like you're using a development installation, but the Next.js")
            print("components are missing. Please make sure you have:")
            print("• Built the frontend: cd cmbagent-ui && npm install && npm run build")
            print("• All required files in the backend/ and cmbagent-ui/ directories")
        else:
            print("The Next.js interface is not available in the pip-installed version.")
            print("This is because pip packages only include Python files, not the full Next.js frontend.")
        print("")
        print("📋 To use the Next.js interface, you have these options:")
        print("")
        print("1. 🔧 Install from source (recommended):")
        print("   git clone https://github.com/CMBAgents/cmbagent.git")
        print("   cd cmbagent")
        print("   pip install -e .")
        print("   cd cmbagent-ui && npm install && npm run build")
        print("   cd .. && cmbagent run --next")
        print("")
        print("2. 🐳 Use Docker (easiest):")
        print("   docker pull docker.io/borisbolliet/cmbagent-ui:latest")
        print("   docker run -p 3000:3000 -p 8000:8000 \\")
        print("     -e OPENAI_API_KEY=\"your-key-here\" \\")
        print("     docker.io/borisbolliet/cmbagent-ui:latest")
        print("")
        sys.exit(1)
    
    # Check if run.py exists
    run_script = backend_path / "run.py"
    if not run_script.exists():
        print("❌ Could not locate backend/run.py")
        print(f"Expected path: {run_script}")
        sys.exit(1)
    
    # Check if package.json exists
    package_json = frontend_path / "package.json"
    if not package_json.exists():
        print("❌ Could not locate frontend/package.json")
        print(f"Expected path: {package_json}")
        print("💡 Make sure Node.js dependencies are installed")
        sys.exit(1)
    
    print("🚀 Starting CMBAgent Full Stack...")
    print("📡 Backend will be available at: http://localhost:8000")
    print("🌐 Frontend will be available at: http://localhost:3000")
    print("📖 API docs: http://localhost:8000/docs")
    print("\n" + "="*50)
    
    backend_process = None
    frontend_process = None
    
    def cleanup(signum=None, frame=None):
        """Cleanup function to stop both processes"""
        print("\n🛑 Stopping servers...")
        if backend_process:
            backend_process.terminate()
            try:
                backend_process.wait(timeout=5)
                print("   Backend server stopped")
            except subprocess.TimeoutExpired:
                backend_process.kill()
                print("   Backend server force killed")
        
        if frontend_process:
            frontend_process.terminate()
            try:
                frontend_process.wait(timeout=5)
                print("   Frontend server stopped")
            except subprocess.TimeoutExpired:
                frontend_process.kill()
                print("   Frontend server force killed")
        
        sys.exit(0)
    
    # Set up signal handlers
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    
    try:
        # Start backend server
        print("🔧 Starting backend server...")
        backend_process = subprocess.Popen(
            [sys.executable, "run.py"],
            cwd=backend_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print("✅ Backend server started")
        
        # Give backend time to start
        time.sleep(3)
        
        # Check if backend is still running
        if backend_process.poll() is not None:
            print("❌ Backend server failed to start")
            stdout, stderr = backend_process.communicate()
            if stderr:
                print(f"Error: {stderr.decode()}")
            sys.exit(1)
        
        # Start frontend server
        print("🔧 Starting frontend server...")
        frontend_process = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=frontend_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print("✅ Frontend server started")
        
        print("\n🎉 CMBAgent Full Stack is running!")
        print("📡 Backend API: http://localhost:8000")
        print("🌐 Frontend UI: http://localhost:3000 (or next available port)")
        print("📖 API Documentation: http://localhost:8000/docs")
        print("\n💡 Press Ctrl+C to stop both servers")
        
        # Wait for both processes
        while True:
            # Check if processes are still running
            backend_running = backend_process.poll() is None
            frontend_running = frontend_process.poll() is None
            
            if not backend_running:
                print("❌ Backend server stopped unexpectedly")
                break
            
            if not frontend_running:
                print("❌ Frontend server stopped unexpectedly")
                break
            
            time.sleep(1)
            
    except KeyboardInterrupt:
        cleanup()
    except Exception as e:
        print(f"❌ Error starting servers: {e}")
        cleanup()
        sys.exit(1)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        prog="cmbagent",
        description="CMBAgent - Multi-Agent System for Scientific Discovery"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Run command - launches Next.js interface
    subparsers.add_parser(
        "run",
        help="Launch the CMBAgent Next.js interface with FastAPI backend"
    )

    args = parser.parse_args()

    if args.command == "run":
        run_next_gui()
    else:
        parser.print_help()
