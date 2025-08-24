# CMBAGENT

A modern Next.js web interface for CMBAgent - an Autonomous Research Backend  platform. This UI provides real-time interaction with CMBAgent's one-shot execution mode, featuring live console output streaming and result visualization.

## Features

- 🚀 **Real-time Execution**: Watch CMBAgent execute tasks with live console output
- 🎯 **One-Shot Mode**: Direct interface to CMBAgent's one_shot functionality
- 🔧 **GPT-4o Integration**: Configured to use GPT-4o model with the engineer agent
- 📊 **Live Console**: Real-time streaming of execution logs and outputs (40% of screen)
- 🎨 **Modern UI**: Clean, responsive interface built with Next.js and Tailwind CSS
- ⚡ **WebSocket Communication**: Fast, real-time updates during task execution
- 📈 **Result Display**: Organized view of execution results and file browser (60% of screen)
- 🖼️ **Inline Plot Viewing**: Click on image files to view plots directly in the interface
- 📁 **File Browser**: Navigate through generated files with CMBAgent folder recognition
- 🗂️ **Working Directory Management**: Configurable work directory with status checking
- 💾 **File Management**: Download, view, and preview generated files and plots

## Architecture

```
┌─────────────────┐    WebSocket    ┌─────────────────┐    Python API    ┌─────────────────┐
│   Next.js UI   │ ←──────────────→ │  FastAPI Server │ ←───────────────→ │    CMBAgent     │
│                 │                 │                 │                   │                 │
│ - Task Input    │                 │ - WebSocket     │                   │ - one_shot()    │
│ - Live Console  │                 │ - Stream Output │                   │ - Engineer      │
│ - Results View  │                 │ - Task Queue    │                   │ - GPT-4o        │
└─────────────────┘                 └─────────────────┘                   └─────────────────┘
```

## Quick Start

### Prerequisites

1. **CMBAgent installed and working** - Make sure you can run CMBAgent from Python
2. **Node.js 18+** - For the Next.js frontend
3. **Python 3.8+** - For the FastAPI backend
4. **OpenAI API Key** - Set as `OPENAI_API_KEY` environment variable

### 1. Set up the Backend

```bash
# Navigate to the backend directory
cd backend

# Install Python dependencies
pip install -r requirements.txt

# Make sure CMBAgent is accessible
# The backend expects to import cmbagent from the parent directory

# Start the FastAPI server
python run.py
```

The backend will start at `http://localhost:8000`

### 2. Set up the Frontend

```bash
# Navigate to the frontend directory
cd cmbagent-ui

# Install Node.js dependencies
npm install

# Start the development server
npm run dev
```

The frontend will start at `http://localhost:3000`

### 3. Test with Simple Task

1. Open `http://localhost:3000` in your browser
2. Enter a simple task like "1 + 1 = ?"
3. Click "Execute Task"
4. Watch the real-time console output as CMBAgent processes the task

## Usage

### Basic Task Execution

1. **Configure Working Directory**: Set your preferred working directory (default: `~/Desktop/cmbdir`)
2. **Enter Task**: Type your task in the text area (e.g., "Plot a sine wave from 0 to 2π")
3. **Configure Settings**: Optionally adjust model, max rounds, and other parameters in advanced settings
4. **Execute**: Click "Execute Task" to start processing
5. **Monitor**: Watch real-time output in the console panel (lower 40% of screen)
6. **View Results**: See execution summary in the Summary tab
7. **Browse Files**: Use the Files tab to navigate generated files and view plots inline

### Example Tasks

- `1 + 1 = ?` - Simple arithmetic
- `Plot a sine wave from 0 to 2π` - Generate and display plots
- `Generate 100 random numbers and plot their histogram` - Data analysis
- `Calculate the factorial of 10` - Mathematical computation
- `Create a simple linear regression example` - Machine learning

### Configuration Options

**Working Directory:**
- **Path**: Set custom working directory (default: `~/Desktop/cmbdir`)
- **Status**: Real-time directory existence checking
- **Management**: Reset, browse, clear, and open directory functions

**Advanced Settings:**
- **Model**: Choose between GPT-4o, GPT-4o Mini, or GPT-4
- **Agent**: Select Engineer (code execution) or Researcher (analysis)
- **Max Rounds**: Maximum conversation rounds (default: 25)
- **Max Attempts**: Maximum retry attempts for failed executions (default: 6)

### File and Plot Management

**Generated Files Structure:**
```
~/Desktop/cmbdir/
└── {task-id}/
    ├── chats/      # Chat history and conversations
    ├── codebase/   # Generated Python code files
    ├── cost/       # Cost analysis and reports
    ├── data/       # Generated data files and plots
    └── time/       # Timing reports and performance data
```

**Plot Viewing:**
1. Navigate to Files tab after task completion
2. Click on the `data` directory (purple folder icon)
3. Click on any PNG/image file to view inline
4. Use download/view buttons for additional actions
5. Click on images to open full-size in new tab

## Development

### Project Structure

```
cmbagent-ui/
├── app/                    # Next.js app directory
│   ├── globals.css        # Global styles
│   ├── layout.tsx         # Root layout
│   └── page.tsx           # Main page
├── components/            # React components
│   ├── Header.tsx         # App header with status
│   ├── TaskInput.tsx      # Task input form with working directory management
│   ├── ConsoleOutput.tsx  # Real-time console with autoscroll
│   ├── ResultDisplay.tsx  # Results viewer with Summary and Files tabs
│   └── FileBrowser.tsx    # File browser with inline plot viewing
├── hooks/                 # Custom React hooks
│   └── useWebSocket.ts    # WebSocket management
└── package.json

backend/
├── main.py               # FastAPI application
├── run.py                # Server startup script
└── requirements.txt      # Python dependencies
```

## Troubleshooting

### Backend Issues

- **Import Error**: Make sure CMBAgent is installed and the backend can import it
- **API Key Error**: Ensure `OPENAI_API_KEY` is set in your environment
- **Port Conflict**: Change the port in `backend/run.py` if 8000 is in use

### Frontend Issues

- **Connection Failed**: Verify the backend is running on port 8000
- **WebSocket Error**: Check browser console for detailed error messages
- **Build Errors**: Run `npm install` to ensure all dependencies are installed

## Testing the Application

To test with the simple "1+1=?" example:

1. Start the backend: `cd backend && python run.py`
2. Start the frontend: `cd cmbagent-ui && npm run dev`
3. Open http://localhost:3000
4. Enter "1 + 1 = ?" in the task input
5. Click "Execute Task"
6. Watch the console for real-time output from CMBAgent

The application should show the engineer agent processing the task and providing the result.
