# Remote Code Execution MVP - Implementation Plan

## Overview

This plan implements remote code execution where:
- **Backend** (Cloud Run) orchestrates agents but does NOT execute code
- **Frontend** (user's machine) executes code in a local Python venv
- **Communication** via WebSocket with reconnection support
- **Persistence** via Firebase Auth + Firestore

## Architecture

```
┌─────────────────┐     WebSocket      ┌─────────────────┐
│    Frontend     │◄──────────────────►│     Backend     │
│  (cmbagent-ui)  │                    │    (FastAPI)    │
├─────────────────┤                    ├─────────────────┤
│ Code Executor   │                    │ Firebase Auth   │
│ (local venv)    │                    │ Firestore       │
│ Execution Store │                    │ Exec Tracker    │
└─────────────────┘                    └─────────────────┘
        │                                      │
        ▼                                      ▼
  ~/cmbagent_workdir/                   GCP Cloud Run
```

## Message Protocol

### Backend → Frontend
| Type | Payload | Description |
|------|---------|-------------|
| `execute_code` | `{execution_id, work_dir, code_blocks, timeout}` | Request code execution |
| `write_file` | `{path, content}` | Write file to work dir |
| `install_packages` | `{packages: string[]}` | Install pip packages |

### Frontend → Backend
| Type | Payload | Description |
|------|---------|-------------|
| `execution_ack` | `{execution_id}` | Acknowledge receipt |
| `execution_result` | `{execution_id, exit_code, output}` | Execution completed |
| `execution_error` | `{execution_id, error}` | Execution failed |
| `files_created` | `{execution_id, files: FileInfo[]}` | New files detected |
| `task_submit` | `{task, config}` | Submit new task |

## Components

### 1. Backend Components

#### `backend/auth.py`
- Firebase Admin SDK integration
- Token verification for HTTP and WebSocket
- User model extraction from JWT

#### `backend/models.py`
- Pydantic models for Firestore documents
- Task, Execution, FileEntry, UserProfile, UserUsage

#### `backend/execution_tracker.py`
- Firestore-backed execution state management
- Create, ack, complete execution records
- Query pending executions for reconnection

#### `backend/main.py` (modifications)
- Add authentication to WebSocket endpoint
- Handle execution_ack, execution_result, files_created messages
- Re-send pending executions on reconnect
- Integrate RemoteWebSocketCodeExecutor

### 2. CMBAgent Components

#### `cmbagent/execution/remote_executor.py`
- Implements AG2 CodeExecutor interface
- Sends code to frontend via callback
- Waits for result with configurable timeout
- Handles reconnection scenarios

#### `cmbagent/base_agent.py` (modifications)
- Accept optional custom executor
- Use RemoteWebSocketCodeExecutor when provided

### 3. Frontend Components

#### `cmbagent-ui/lib/firebase.ts`
- Firebase client initialization
- Auth state management

#### `cmbagent-ui/lib/codeExecutor.ts`
- Python venv management
- Code file writing and execution
- File change detection
- Package installation

#### `cmbagent-ui/lib/executionStore.ts`
- Persist pending executions to disk
- Survive app restarts
- Queue completed results for sending

#### `cmbagent-ui/app/api/execute/route.ts`
- Server-side API route for code execution
- Bridges WebSocket messages to local executor

#### `cmbagent-ui/hooks/useWebSocket.ts` (modifications)
- Handle execute_code messages
- Send ack immediately
- Execute via API route
- Send results back
- Reconnection with result flush

### 4. Deployment

#### `backend/Dockerfile`
- Python 3.12 slim base
- FastAPI + dependencies
- Cloud Run compatible

#### `cloudbuild.yaml`
- Cloud Build configuration
- Auto-deploy to Cloud Run

## Firestore Schema

```
/users/{uid}
  - email: string
  - name: string
  - created_at: timestamp
  - plan: "free" | "pro"

/users/{uid}/usage
  - tasks_run: number
  - total_cost_usd: number
  - last_active: timestamp

/tasks/{task_id}
  - user_id: string
  - prompt: string
  - config: map
  - status: "pending" | "running" | "completed" | "failed"
  - created_at: timestamp
  - completed_at: timestamp?
  - file_registry: map<path, FileEntry>
  - total_cost_usd: number

/executions/{execution_id}
  - task_id: string
  - user_id: string
  - status: "pending" | "acked" | "running" | "completed" | "failed"
  - code_blocks: array
  - created_at: timestamp
  - deadline: timestamp?
  - acked_at: timestamp?
  - completed_at: timestamp?
  - result: map?
```

## Implementation Order

### Phase 1: Backend Infrastructure (Week 1)
- [x] Plan documented
- [ ] Firebase project setup
- [ ] backend/auth.py
- [ ] backend/models.py
- [ ] backend/execution_tracker.py
- [ ] cmbagent/execution/remote_executor.py
- [ ] Integration tests (local)

### Phase 2: Frontend Execution (Week 2)
- [ ] cmbagent-ui/lib/firebase.ts
- [ ] cmbagent-ui/lib/codeExecutor.ts
- [ ] cmbagent-ui/lib/executionStore.ts
- [ ] cmbagent-ui/app/api/execute/route.ts
- [ ] Update useWebSocket.ts
- [ ] End-to-end tests

### Phase 3: Deployment (Week 3)
- [ ] backend/Dockerfile
- [ ] Cloud Run deployment
- [ ] Firebase Auth in frontend UI
- [ ] Production testing
- [ ] Documentation

## Configuration

### Environment Variables (Backend)
```
GOOGLE_CLOUD_PROJECT=your-project-id
FIREBASE_CREDENTIALS=path/to/credentials.json (local only)
```

### Environment Variables (Frontend)
```
NEXT_PUBLIC_FIREBASE_API_KEY=...
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=...
NEXT_PUBLIC_FIREBASE_PROJECT_ID=...
NEXT_PUBLIC_BACKEND_URL=https://cmbagent-backend-xxx.run.app
```

## Cost Estimate (MVP)

| Service | Monthly Cost |
|---------|-------------|
| Cloud Run | $5-20 (scales to zero) |
| Firestore | $0-5 (free tier) |
| Firebase Auth | Free (50k MAU) |
| **Total** | **~$5-25/mo** |

## Key Design Decisions

1. **No Redis for MVP** - Firestore handles persistence, real-time not critical for long jobs
2. **Single Cloud Run instance initially** - Scale later if needed
3. **Frontend executes code** - Backend never runs user code
4. **Async execution with reconnection** - Handles multi-hour jobs
5. **File registry on backend** - Track what exists on frontend without storing files
