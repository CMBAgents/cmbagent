#!/usr/bin/env python3
"""
Comprehensive backend API testing script for CMBAgent
Tests all endpoints to verify functionality after major overhaul
"""

import asyncio
import json
import os
import sys
from pathlib import Path
import aiohttp
import websockets
import uuid

# Configuration
BASE_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"

class Colors:
    """ANSI color codes for terminal output"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_test(name: str):
    """Print test header"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}Testing: {name}{Colors.RESET}")

def print_success(message: str):
    """Print success message"""
    print(f"  {Colors.GREEN}✓ {message}{Colors.RESET}")

def print_error(message: str):
    """Print error message"""
    print(f"  {Colors.RED}✗ {message}{Colors.RESET}")

def print_warning(message: str):
    """Print warning message"""
    print(f"  {Colors.YELLOW}⚠ {message}{Colors.RESET}")

async def test_health_endpoint():
    """Test /api/health endpoint"""
    print_test("Health Check Endpoint")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/api/health") as response:
                if response.status == 200:
                    data = await response.json()
                    print_success(f"Health check passed: {data}")
                    return True
                else:
                    print_error(f"Health check failed with status {response.status}")
                    return False
    except Exception as e:
        print_error(f"Health check error: {e}")
        return False

async def test_root_endpoint():
    """Test / root endpoint"""
    print_test("Root Endpoint")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/") as response:
                if response.status == 200:
                    data = await response.json()
                    print_success(f"Root endpoint accessible: {data}")
                    return True
                else:
                    print_error(f"Root endpoint failed with status {response.status}")
                    return False
    except Exception as e:
        print_error(f"Root endpoint error: {e}")
        return False

async def test_credentials_status():
    """Test /api/credentials/status endpoint"""
    print_test("Credentials Status Endpoint")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/api/credentials/status") as response:
                if response.status == 200:
                    data = await response.json()
                    print_success(f"Credentials status retrieved")
                    print(f"    OpenAI: {data.get('openai', {}).get('valid', False)}")
                    print(f"    Anthropic: {data.get('anthropic', {}).get('valid', False)}")
                    print(f"    Vertex: {data.get('vertex', {}).get('valid', False)}")
                    return True
                else:
                    print_error(f"Credentials status failed with status {response.status}")
                    return False
    except Exception as e:
        print_error(f"Credentials status error: {e}")
        return False

async def test_credentials_test_all():
    """Test /api/credentials/test-all endpoint"""
    print_test("Test All Credentials Endpoint")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/api/credentials/test-all") as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get('results', {})
                    print_success(f"Credentials tested")
                    for provider, status_obj in results.items():
                        # status_obj is a dict with 'provider', 'status', 'message', etc.
                        status = status_obj.get('status', 'unknown')
                        message = status_obj.get('message', 'No message')
                        valid = status == 'valid'
                        symbol = "✓" if valid else "✗"
                        print(f"    {symbol} {provider}: {message}")
                    return True
                else:
                    print_error(f"Credentials test-all failed with status {response.status}")
                    return False
    except Exception as e:
        print_error(f"Credentials test-all error: {e}")
        return False

async def test_file_operations():
    """Test file operation endpoints"""
    print_test("File Operations Endpoints")

    # Create a test directory and file
    test_dir = Path("/tmp/cmbagent_test")
    test_dir.mkdir(exist_ok=True)
    test_file = test_dir / "test.txt"
    test_file.write_text("Test content for CMBAgent")

    results = []

    try:
        async with aiohttp.ClientSession() as session:
            # Test list directory
            async with session.get(f"{BASE_URL}/api/files/list",
                                   params={"path": str(test_dir)}) as response:
                if response.status == 200:
                    data = await response.json()
                    print_success(f"Directory listing works, found {len(data.get('items', []))} items")
                    results.append(True)
                else:
                    print_error(f"Directory listing failed with status {response.status}")
                    results.append(False)

            # Test read file content
            async with session.get(f"{BASE_URL}/api/files/content",
                                   params={"path": str(test_file)}) as response:
                if response.status == 200:
                    data = await response.json()
                    content = data.get('content', '')
                    if "Test content" in content:
                        print_success(f"File reading works correctly")
                        results.append(True)
                    else:
                        print_error(f"File content doesn't match expected")
                        results.append(False)
                else:
                    print_error(f"File reading failed with status {response.status}")
                    results.append(False)

            # Test clear directory
            async with session.delete(f"{BASE_URL}/api/files/clear-directory",
                                      params={"path": str(test_dir)}) as response:
                if response.status == 200:
                    print_success(f"Directory clearing works")
                    results.append(True)
                else:
                    print_error(f"Directory clearing failed with status {response.status}")
                    results.append(False)

    except Exception as e:
        print_error(f"File operations error: {e}")
        results.append(False)

    # Cleanup
    try:
        test_dir.rmdir()
    except:
        pass

    return all(results)

async def test_websocket_connection():
    """Test WebSocket connection and basic communication"""
    print_test("WebSocket Connection")

    task_id = str(uuid.uuid4())
    ws_url = f"{WS_URL}/ws/{task_id}"

    try:
        async with websockets.connect(ws_url) as websocket:
            print_success("WebSocket connection established")

            # Try sending a simple message
            test_message = {
                "task": "print('Hello from test')",
                "config": {
                    "mode": "one_shot",
                    "model": "gpt-4o",
                    "work_dir": "/tmp/cmbagent_test_ws"
                }
            }

            await websocket.send(json.dumps(test_message))
            print_success("Message sent to WebSocket")

            # Wait for a few messages (timeout after 5 seconds)
            message_count = 0
            try:
                for _ in range(10):  # Receive up to 10 messages
                    message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    data = json.loads(message)
                    message_count += 1

                    msg_type = data.get('type', 'unknown')
                    if msg_type == 'heartbeat':
                        continue

                    print(f"    Received {msg_type}: {str(data)[:100]}...")

                    if msg_type == 'complete':
                        print_success(f"WebSocket communication successful ({message_count} messages)")
                        return True

            except asyncio.TimeoutError:
                print_warning(f"WebSocket timeout after receiving {message_count} messages")
                return message_count > 0

            return message_count > 0

    except Exception as e:
        print_error(f"WebSocket connection error: {e}")
        return False

async def test_task_submit_endpoint():
    """Test /api/task/submit endpoint"""
    print_test("Task Submit Endpoint")

    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "task": "Test task",
                "model": "gpt-4o",
                "mode": "one_shot"
            }

            async with session.post(f"{BASE_URL}/api/task/submit",
                                   json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    task_id = data.get('task_id')
                    if task_id:
                        print_success(f"Task submit works, got task_id: {task_id}")
                        return True
                    else:
                        print_error("Task submit didn't return task_id")
                        return False
                else:
                    print_error(f"Task submit failed with status {response.status}")
                    return False
    except Exception as e:
        print_error(f"Task submit error: {e}")
        return False

async def check_backend_running():
    """Check if backend is running"""
    print_test("Backend Availability Check")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/api/health", timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    print_success("Backend is running on http://localhost:8000")
                    return True
    except Exception as e:
        print_error(f"Backend is NOT running: {e}")
        print_warning("Please start the backend with: cd backend && python run.py")
        return False

async def main():
    """Run all tests"""
    print(f"\n{Colors.BOLD}{'='*60}")
    print(f"CMBAgent Backend API Test Suite")
    print(f"{'='*60}{Colors.RESET}\n")

    # Check if backend is running first
    if not await check_backend_running():
        print(f"\n{Colors.RED}{Colors.BOLD}Tests cannot proceed without backend running{Colors.RESET}")
        sys.exit(1)

    # Run all tests
    results = {
        "Root Endpoint": await test_root_endpoint(),
        "Health Check": await test_health_endpoint(),
        "Credentials Status": await test_credentials_status(),
        "Credentials Test All": await test_credentials_test_all(),
        "File Operations": await test_file_operations(),
        "Task Submit": await test_task_submit_endpoint(),
        "WebSocket Connection": await test_websocket_connection(),
    }

    # Summary
    print(f"\n{Colors.BOLD}{'='*60}")
    print(f"Test Summary")
    print(f"{'='*60}{Colors.RESET}\n")

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, result in results.items():
        symbol = f"{Colors.GREEN}✓" if result else f"{Colors.RED}✗"
        print(f"{symbol} {test_name}{Colors.RESET}")

    print(f"\n{Colors.BOLD}Results: {passed}/{total} tests passed{Colors.RESET}")

    if passed == total:
        print(f"{Colors.GREEN}{Colors.BOLD}All tests passed!{Colors.RESET}\n")
        sys.exit(0)
    else:
        print(f"{Colors.RED}{Colors.BOLD}Some tests failed!{Colors.RESET}\n")
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Tests interrupted by user{Colors.RESET}")
        sys.exit(1)
