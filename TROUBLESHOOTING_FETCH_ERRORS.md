# Troubleshooting: "Failed to Fetch" Errors for Cost and Files

**Issue**: Getting "failed to fetch" errors when trying to view cost data or browse files in the UI.

## Quick Diagnosis

Run this command to check if both servers are running:

```bash
# Check backend (port 8000)
curl http://localhost:8000/api/health

# Check frontend (port 3000)
curl http://localhost:3000/api/health

# Check frontend proxy is working
curl "http://localhost:3000/api/files/list?path=/tmp"
```

## Common Causes & Solutions

### 1. ✅ Backend Not Running

**Symptom**: Browser console shows "Failed to fetch" or "ERR_CONNECTION_REFUSED"

**Solution**:
```bash
cd /Users/boris/GitHub/cmbagent/backend
python run.py
```

**Verify**: You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

---

### 2. ✅ Frontend Not Running

**Symptom**: Cannot access http://localhost:3000 at all

**Solution**:
```bash
cd /Users/boris/GitHub/cmbagent/cmbagent-ui
npm run dev
```

**Verify**: You should see:
```
▲ Next.js 14.0.4
- Local:        http://localhost:3000
✓ Ready in 1056ms
```

---

### 3. ✅ Port Conflicts (Multiple Backend Processes)

**Symptom**: Backend appears to be running but endpoints don't work correctly

**Diagnosis**:
```bash
lsof -ti:8000
# If you see multiple PIDs, you have multiple processes running
```

**Solution**:
```bash
# Kill all processes on port 8000
pkill -f "python run.py"
# Or kill specific PIDs
kill -9 $(lsof -ti:8000)

# Restart backend cleanly
cd /Users/boris/GitHub/cmbagent/backend
python run.py
```

---

### 4. ✅ Files Don't Exist Yet

**Symptom**: Errors appear immediately when opening the UI, before running any tasks

**Why**: The ResultDisplay component tries to load cost data automatically if there are results from a previous session.

**Solutions**:

**Option A**: Run a simple task first
```javascript
// In the UI:
Task: "print('Hello World')"
Mode: One Shot
Click Submit
```

**Option B**: Clear the browser's local storage
```javascript
// Open browser console (F12) and run:
localStorage.clear()
// Then refresh the page
```

---

### 5. ✅ Next.js Proxy Not Working

**Symptom**: Backend works directly (curl localhost:8000) but not through frontend (localhost:3000)

**Diagnosis**:
```bash
# This should work:
curl http://localhost:8000/api/health

# This should also work:
curl http://localhost:3000/api/health

# If the second fails, proxy is broken
```

**Solution**: Restart the Next.js dev server:
```bash
cd /Users/boris/GitHub/cmbagent/cmbagent-ui
# Kill existing
pkill -f "next dev"
# Restart
npm run dev
```

---

### 6. ✅ Invalid Work Directory Path

**Symptom**: Errors when trying to browse files or view results

**Check**: Look at the "Working Directory" setting in the UI. Common issues:
- Path contains `~` but isn't being expanded
- Path doesn't exist
- Path has no permission

**Solution**: Use absolute paths in the Advanced Configuration:
```bash
# Instead of:
~/cmbagent_workdir

# Use:
/Users/boris/cmbagent_workdir
```

**Or create the directory first**:
```bash
mkdir -p ~/cmbagent_workdir
```

---

### 7. ✅ CORS Issues (Browser Security)

**Symptom**: Browser console shows CORS errors like:
```
Access to fetch at 'http://localhost:8000/api/files/list' from origin
'http://localhost:3000' has been blocked by CORS policy
```

**Check**: Open browser DevTools (F12) → Console tab

**Solution**: This shouldn't happen in development, but if it does:

1. Verify backend CORS settings in `backend/main.py`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        # ... more ports
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

2. Restart the backend after any changes

---

### 8. ✅ Task Still Running

**Symptom**: "Failed to fetch" errors appear while a task is actively running

**Why**: The cost report files are only written when the task completes. While the task is running, the files don't exist yet.

**Solution**: This is expected behavior. Wait for the task to complete:
- Console output shows task progress
- When complete, you'll see "Task execution completed"
- Cost data will then be available

---

## Diagnostic Script

Save this as `test_servers.sh` and run it:

```bash
#!/bin/bash

echo "=== CMBAgent Server Diagnostics ==="
echo ""

# Test backend
echo "1. Testing Backend (port 8000)..."
if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "   ✓ Backend is running"
else
    echo "   ✗ Backend is NOT running"
    echo "   → Run: cd backend && python run.py"
fi

# Test frontend
echo ""
echo "2. Testing Frontend (port 3000)..."
if curl -s http://localhost:3000 > /dev/null 2>&1; then
    echo "   ✓ Frontend is running"
else
    echo "   ✗ Frontend is NOT running"
    echo "   → Run: cd cmbagent-ui && npm run dev"
fi

# Test proxy
echo ""
echo "3. Testing Next.js Proxy..."
if curl -s http://localhost:3000/api/health > /dev/null 2>&1; then
    echo "   ✓ Proxy is working"
else
    echo "   ✗ Proxy is NOT working"
    echo "   → Restart frontend: pkill -f 'next dev' && npm run dev"
fi

# Check for port conflicts
echo ""
echo "4. Checking for port conflicts..."
BACKEND_PIDS=$(lsof -ti:8000 2>/dev/null | wc -l)
if [ "$BACKEND_PIDS" -eq 1 ]; then
    echo "   ✓ One backend process running"
elif [ "$BACKEND_PIDS" -gt 1 ]; then
    echo "   ⚠ Multiple backend processes detected!"
    echo "   → Run: pkill -f 'python run.py' && cd backend && python run.py"
else
    echo "   ✗ No backend process found"
fi

# Test file operations
echo ""
echo "5. Testing File Operations..."
TEST_DIR="/tmp"
if curl -s "http://localhost:3000/api/files/list?path=$TEST_DIR" | jq . > /dev/null 2>&1; then
    echo "   ✓ File listing works"
else
    echo "   ✗ File listing failed"
fi

echo ""
echo "=== End Diagnostics ==="
```

Make it executable and run:
```bash
chmod +x test_servers.sh
./test_servers.sh
```

---

## Browser Console Debugging

1. Open browser DevTools: `F12` or `Cmd+Option+I` (Mac)
2. Go to **Console** tab
3. Look for errors in red
4. Common error messages and meanings:

| Error Message | Meaning | Solution |
|---------------|---------|----------|
| `Failed to fetch` | Network error, server not running | Start backend/frontend |
| `404 Not Found` | File/directory doesn't exist | Run a task first, or check path |
| `CORS policy` | Cross-origin blocked | Check CORS config in backend |
| `ERR_CONNECTION_REFUSED` | Server not listening on port | Start the server |
| `TypeError: Failed to fetch` | Generic network error | Check both servers running |

---

## Correct Startup Sequence

**Terminal 1 - Backend**:
```bash
cd /Users/boris/GitHub/cmbagent/backend
python run.py
# Leave this running
```

**Terminal 2 - Frontend**:
```bash
cd /Users/boris/GitHub/cmbagent/cmbagent-ui
npm run dev
# Leave this running
```

**Browser**:
```
Open: http://localhost:3000
```

---

## Still Having Issues?

### Check Backend Logs
```bash
tail -f /tmp/backend.log
```

### Check Frontend Logs
Look at the terminal where `npm run dev` is running

### Test Endpoints Directly

```bash
# Health check
curl http://localhost:8000/api/health | jq .

# List files
curl "http://localhost:8000/api/files/list?path=~/cmbagent_workdir" | jq .

# Get file content (use actual path)
curl "http://localhost:8000/api/files/content?path=/path/to/file.json" | jq .
```

### Clear Everything and Restart

```bash
# Kill all processes
pkill -f "python run.py"
pkill -f "next dev"

# Wait a moment
sleep 2

# Start backend
cd /Users/boris/GitHub/cmbagent/backend
python run.py &

# Wait for backend to start
sleep 3

# Start frontend
cd /Users/boris/GitHub/cmbagent/cmbagent-ui
npm run dev
```

---

## Expected Behavior

### Before Running Any Tasks
- ✅ UI should load without errors
- ✅ Credentials panel should work
- ⚠️ File browser might show errors (no files exist yet) - **this is normal**
- ⚠️ Results panel might show "Cost directory not found" - **this is normal**

### After Running a Task
- ✅ Console output appears in real-time
- ✅ Results appear when task completes
- ✅ Cost breakdown loads automatically
- ✅ File browser shows task directory structure

### While Task is Running
- ✅ Console output streams in real-time
- ⚠️ Cost data not available yet - **this is normal**
- ✅ "Processing..." indicator shows task is active
