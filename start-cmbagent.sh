#!/bin/bash

# CMBAgent Full Stack Launcher
# This script starts both the backend and frontend with one command
# PREREQUISITE: Activate your Python virtual environment before running this script

set -e  # Exit on any error

echo "🚀 Starting CMBAgent Full Stack..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/cmbagent-ui"

# Function to cleanup background processes
cleanup() {
    echo -e "\n${YELLOW}🛑 Stopping servers...${NC}"
    if [ ! -z "$BACKEND_PID" ]; then
        kill $BACKEND_PID 2>/dev/null || true
        echo -e "${YELLOW}   Backend server stopped${NC}"
    fi
    if [ ! -z "$FRONTEND_PID" ]; then
        kill $FRONTEND_PID 2>/dev/null || true
        echo -e "${YELLOW}   Frontend server stopped${NC}"
    fi
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM

# Check if directories exist
if [ ! -d "$BACKEND_DIR" ]; then
    echo -e "${RED}❌ Backend directory not found: $BACKEND_DIR${NC}"
    exit 1
fi

if [ ! -d "$FRONTEND_DIR" ]; then
    echo -e "${RED}❌ Frontend directory not found: $FRONTEND_DIR${NC}"
    exit 1
fi

echo -e "${BLUE}🔧 Starting backend server...${NC}"
cd "$BACKEND_DIR"
python run.py &
BACKEND_PID=$!
echo -e "${GREEN}✅ Backend server started (PID: $BACKEND_PID)${NC}"
echo -e "${GREEN}   Backend available at: http://localhost:8000${NC}"

# Give backend time to start
sleep 3

echo -e "${BLUE}🔧 Starting frontend server...${NC}"
cd "$FRONTEND_DIR"
npm run dev &
FRONTEND_PID=$!
echo -e "${GREEN}✅ Frontend server started (PID: $FRONTEND_PID)${NC}"
echo -e "${GREEN}   Frontend will open automatically in your browser${NC}"

echo -e "\n${GREEN}🎉 CMBAgent Full Stack is running!${NC}"
echo -e "${BLUE}📡 Backend API: http://localhost:8000${NC}"
echo -e "${BLUE}🌐 Frontend UI: http://localhost:3000 (or next available port)${NC}"
echo -e "${BLUE}📖 API Documentation: http://localhost:8000/docs${NC}"
echo -e "\n${YELLOW}💡 Press Ctrl+C to stop both servers${NC}"

# Wait for both processes
wait $BACKEND_PID $FRONTEND_PID