#!/bin/bash

# CMBAGENT Startup Script
# This script starts both the backend and frontend servers

echo "🚀 Starting CMBAGENT..."
echo "================================"

# Check if we're in the right directory
if [ ! -d "cmbagent-ui" ] || [ ! -d "backend" ]; then
    echo "❌ Error: Please run this script from the directory containing 'cmbagent-ui' and 'backend' folders"
    exit 1
fi

# Check if Node.js is installed
if ! command -v node &> /dev/null; then
    echo "❌ Error: Node.js is not installed. Please install Node.js 18+ first."
    exit 1
fi

# Check if Python is installed
if ! command -v python &> /dev/null && ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python is not installed. Please install Python 3.8+ first."
    exit 1
fi

# Use python3 if available, otherwise python
PYTHON_CMD="python"
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
fi

echo "📦 Installing dependencies..."

# Install backend dependencies
echo "Installing Python dependencies..."
cd backend
pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "❌ Failed to install Python dependencies"
    exit 1
fi
cd ..

# Install frontend dependencies
echo "Installing Node.js dependencies..."
cd cmbagent-ui
npm install
if [ $? -ne 0 ]; then
    echo "❌ Failed to install Node.js dependencies"
    exit 1
fi
cd ..

echo "✅ Dependencies installed successfully!"
echo ""

# Function to cleanup background processes
cleanup() {
    echo ""
    echo "🛑 Shutting down servers..."
    if [ ! -z "$BACKEND_PID" ]; then
        kill $BACKEND_PID 2>/dev/null
    fi
    if [ ! -z "$FRONTEND_PID" ]; then
        kill $FRONTEND_PID 2>/dev/null
    fi
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM

echo "🔧 Starting backend server..."
cd backend
$PYTHON_CMD run.py &
BACKEND_PID=$!
cd ..

# Wait a moment for backend to start
sleep 3

echo "🎨 Starting frontend server..."
cd cmbagent-ui
npm run dev &
FRONTEND_PID=$!
cd ..

echo ""
echo "✅ CMBAGENT is starting up!"
echo "================================"
echo "🔗 Frontend: http://localhost:3000"
echo "🔗 Backend:  http://localhost:8000"
echo "📖 API Docs: http://localhost:8000/docs"
echo ""
echo "💡 Make sure your OPENAI_API_KEY environment variable is set!"
echo ""
echo "Press Ctrl+C to stop both servers"

# Wait for both processes
wait $BACKEND_PID $FRONTEND_PID
