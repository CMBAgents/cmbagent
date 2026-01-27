import { useEffect, useRef, useState, useCallback } from 'react'
import { getIdToken, isFirebaseAvailable } from '@/lib/firebase'

// Note: ExecutionStore uses Node.js fs and runs server-side only via API routes

interface WebSocketMessage {
  type: 'output' | 'status' | 'result' | 'error' | 'complete' | 'heartbeat' |
        'execute_code' | 'write_file' | 'install_packages'
  task_id?: string
  execution_id?: string
  data?: any
  message?: string
  timestamp?: number
  // For execute_code
  work_dir?: string
  code_blocks?: Array<{ code: string; language: string }>
  timeout?: number
  // For write_file
  path?: string
  content?: string
  // For install_packages
  packages?: string[]
}

interface UseWebSocketProps {
  onOutput: (output: string) => void
  onResult: (result: any) => void
  onError: (error: string) => void
  onComplete: () => void
  onStatusChange: (status: string) => void
  onExecutionStart?: (executionId: string) => void
  onExecutionComplete?: (executionId: string, result: any) => void
}

interface UseWebSocketReturn {
  connect: (taskId: string, task: string, config: any) => Promise<void>
  disconnect: () => void
  sendMessage: (message: any) => void
  isConnected: boolean
  isConnecting: boolean
  taskId: string | null
  isExecuting: boolean
}

// Backend URL configuration
const getBackendUrl = (): string => {
  if (typeof window !== 'undefined') {
    return process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'
  }
  return 'http://localhost:8000'
}

export function useWebSocket({
  onOutput,
  onResult,
  onError,
  onComplete,
  onStatusChange,
  onExecutionStart,
  onExecutionComplete,
}: UseWebSocketProps): UseWebSocketReturn {
  const [isConnected, setIsConnected] = useState(false)
  const [isConnecting, setIsConnecting] = useState(false)
  const [isExecuting, setIsExecuting] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const taskIdRef = useRef<string | null>(null)
  const reconnectAttemptsRef = useRef(0)
  const maxReconnectAttempts = 10

  /**
   * Execute code via the local API route
   */
  const executeCode = useCallback(async (
    executionId: string,
    workDir: string,
    codeBlocks: Array<{ code: string; language: string }>,
    timeout: number
  ) => {
    setIsExecuting(true)
    onExecutionStart?.(executionId)
    onOutput(`\n--- Executing code (${codeBlocks.length} block(s)) ---\n`)

    try {
      const response = await fetch('/api/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workDir,
          codeBlocks,
          timeout,
        }),
      })

      const data = await response.json()

      if (data.success && data.result) {
        const result = data.result

        // Show output to user
        if (result.output) {
          onOutput(result.output)
        }

        // Report result to backend
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({
            type: 'execution_result',
            execution_id: executionId,
            result: {
              exit_code: result.exitCode,
              output: result.output,
              code_file: result.codeFile,
            },
          }))

          // Report files created
          if (result.filesCreated && result.filesCreated.length > 0) {
            wsRef.current.send(JSON.stringify({
              type: 'files_created',
              execution_id: executionId,
              files: result.filesCreated,
            }))
            onOutput(`\nCreated ${result.filesCreated.length} file(s)\n`)
          }
        }

        onExecutionComplete?.(executionId, result)
        onOutput(`\n--- Execution complete (exit code: ${result.exitCode}) ---\n`)

      } else {
        throw new Error(data.error || 'Execution failed')
      }

    } catch (error: any) {
      const errorMsg = error.message || 'Unknown execution error'
      onError(`Execution error: ${errorMsg}`)

      // Report error to backend
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({
          type: 'execution_error',
          execution_id: executionId,
          error: errorMsg,
        }))
      }

    } finally {
      setIsExecuting(false)
    }
  }, [onOutput, onError, onExecutionStart, onExecutionComplete])

  /**
   * Install packages via the local API route
   */
  const installPackages = useCallback(async (packages: string[], workDir: string) => {
    onOutput(`\n--- Installing packages: ${packages.join(', ')} ---\n`)

    try {
      const response = await fetch('/api/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workDir, packages }),
      })

      const data = await response.json()

      if (data.success) {
        onOutput(`Packages installed successfully\n`)
        if (data.output) {
          onOutput(data.output)
        }
      } else {
        onOutput(`Package installation failed: ${data.error}\n`)
        if (data.failed && data.failed.length > 0) {
          onOutput(`Failed packages: ${data.failed.join(', ')}\n`)
        }
      }

      // Notify backend
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({
          type: 'install_complete',
          packages,
          success: data.success,
          failed: data.failed,
        }))
      }

    } catch (error: any) {
      onError(`Install error: ${error.message}`)
    }
  }, [onOutput, onError])

  /**
   * Write a file via the local API route
   */
  const writeFile = useCallback(async (path: string, content: string, workDir: string) => {
    try {
      const response = await fetch('/api/files', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workDir, path, content }),
      })

      const data = await response.json()

      if (data.success) {
        onOutput(`File written: ${path}\n`)
      } else {
        onError(`Failed to write file: ${data.error}`)
      }

    } catch (error: any) {
      onError(`File write error: ${error.message}`)
    }
  }, [onOutput, onError])

  /**
   * Flush any pending execution results on reconnect.
   *
   * For now, this is a no-op since execution results are sent immediately.
   * In the future, this could call a server-side API to check for cached results.
   */
  const flushPendingResults = useCallback(async () => {
    // TODO: Implement server-side pending results via API route
    // The executionStore.ts runs server-side only
    // For now, results are sent immediately after execution completes
    console.log('Checking for pending execution results...')
  }, [])

  /**
   * Handle incoming WebSocket messages
   */
  const handleMessage = useCallback(async (event: MessageEvent) => {
    try {
      const message: WebSocketMessage = JSON.parse(event.data)

      switch (message.type) {
        case 'output':
          if (message.data) {
            onOutput(message.data)
          }
          break

        case 'status':
          if (message.message) {
            onStatusChange(message.message)
            onOutput(`Status: ${message.message}\n`)
          }
          break

        case 'result':
          if (message.data) {
            onResult(message.data)
            onOutput('Results received\n')
          }
          break

        case 'error':
          if (message.message) {
            onError(message.message)
            onOutput(`Error: ${message.message}\n`)
          }
          break

        case 'complete':
          onComplete()
          onOutput('Task execution completed\n')
          break

        case 'heartbeat':
          // Just keep the connection alive
          break

        case 'execute_code':
          // Backend requesting code execution
          if (message.execution_id && message.code_blocks && message.work_dir) {
            // Send acknowledgment immediately
            if (wsRef.current?.readyState === WebSocket.OPEN) {
              wsRef.current.send(JSON.stringify({
                type: 'execution_ack',
                execution_id: message.execution_id,
              }))
            }

            // Execute code (don't await - let it run in background)
            executeCode(
              message.execution_id,
              message.work_dir,
              message.code_blocks,
              message.timeout || 86400
            )
          }
          break

        case 'write_file':
          // Backend requesting file write
          if (message.path && message.content !== undefined && message.work_dir) {
            await writeFile(message.path, message.content, message.work_dir)
          }
          break

        case 'install_packages':
          // Backend requesting package installation
          if (message.packages && message.work_dir) {
            await installPackages(message.packages, message.work_dir)
          }
          break

        default:
          // Unknown message type - log and ignore
          console.log('Unknown message type:', message.type)
      }
    } catch (error) {
      console.error('Error parsing WebSocket message:', error)
      onError('Error parsing server response')
    }
  }, [onOutput, onResult, onError, onComplete, onStatusChange, executeCode, writeFile, installPackages])

  /**
   * Connect to WebSocket with optional authentication
   */
  const connect = useCallback(async (taskId: string, task: string, config: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.close()
    }

    setIsConnecting(true)
    taskIdRef.current = taskId
    reconnectAttemptsRef.current = 0

    try {
      // Get auth token if Firebase is available
      let token: string | null = null
      if (isFirebaseAvailable()) {
        token = await getIdToken()
      }

      // Build WebSocket URL
      const backendUrl = getBackendUrl()
      const wsProtocol = backendUrl.startsWith('https') ? 'wss' : 'ws'
      const wsHost = backendUrl.replace(/^https?:\/\//, '')
      let wsUrl = `${wsProtocol}://${wsHost}/ws/${taskId}`

      if (token) {
        wsUrl += `?token=${encodeURIComponent(token)}`
      }

      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = async () => {
        setIsConnected(true)
        setIsConnecting(false)
        reconnectAttemptsRef.current = 0
        onStatusChange('Connected to CMBAgent backend')

        // Flush any pending results from previous session
        await flushPendingResults()

        // Send task data
        ws.send(JSON.stringify({
          type: 'task_submit',
          task,
          config
        }))
      }

      ws.onmessage = handleMessage

      ws.onerror = (error) => {
        console.error('WebSocket error:', error)
        onError('WebSocket connection error')
        setIsConnecting(false)
      }

      ws.onclose = (event) => {
        setIsConnected(false)
        setIsConnecting(false)

        if (event.code !== 1000) { // Not a normal closure
          onError('Connection lost to CMBAgent backend')

          // Attempt reconnection
          if (reconnectAttemptsRef.current < maxReconnectAttempts) {
            const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 30000)
            reconnectAttemptsRef.current++

            onOutput(`Reconnecting in ${delay/1000}s (attempt ${reconnectAttemptsRef.current}/${maxReconnectAttempts})...\n`)

            setTimeout(() => {
              if (taskIdRef.current === taskId) {
                connect(taskId, task, config)
              }
            }, delay)
          } else {
            onError('Max reconnection attempts reached')
          }
        }
      }

    } catch (error) {
      console.error('Error creating WebSocket:', error)
      onError('Failed to connect to CMBAgent backend')
      setIsConnecting(false)
    }
  }, [onOutput, onError, onStatusChange, handleMessage, flushPendingResults])

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close(1000, 'User disconnected')
      wsRef.current = null
    }
    setIsConnected(false)
    setIsConnecting(false)
    setIsExecuting(false)
    taskIdRef.current = null
    reconnectAttemptsRef.current = 0
  }, [])

  const sendMessage = useCallback((message: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message))
    }
  }, [])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      disconnect()
    }
  }, [disconnect])

  return {
    connect,
    disconnect,
    sendMessage,
    isConnected,
    isConnecting,
    taskId: taskIdRef.current,
    isExecuting,
  }
}
