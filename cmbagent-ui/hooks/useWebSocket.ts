import { useEffect, useRef, useState, useCallback } from 'react'

interface WebSocketMessage {
  type: 'output' | 'status' | 'result' | 'error' | 'complete' | 'heartbeat'
  task_id?: string
  data?: any
  message?: string
  timestamp?: number
}

interface UseWebSocketProps {
  onOutput: (output: string) => void
  onResult: (result: any) => void
  onError: (error: string) => void
  onComplete: () => void
  onStatusChange: (status: string) => void
}

export function useWebSocket({
  onOutput,
  onResult,
  onError,
  onComplete,
  onStatusChange
}: UseWebSocketProps) {
  const [isConnected, setIsConnected] = useState(false)
  const [isConnecting, setIsConnecting] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const taskIdRef = useRef<string | null>(null)

  const connect = useCallback(async (taskId: string, task: string, config: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.close()
    }

    setIsConnecting(true)
    taskIdRef.current = taskId

    try {
      const ws = new WebSocket(`ws://localhost:8000/ws/${taskId}`)
      wsRef.current = ws

      ws.onopen = () => {
        setIsConnected(true)
        setIsConnecting(false)
        onStatusChange('Connected to CMBAgent backend')
        
        // Send task data
        ws.send(JSON.stringify({ task, config }))
      }

      ws.onmessage = (event) => {
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
                onOutput(`📊 ${message.message}`)
              }
              break
              
            case 'result':
              if (message.data) {
                onResult(message.data)
                onOutput('🎯 Results received')
              }
              break
              
            case 'error':
              if (message.message) {
                onError(message.message)
                onOutput(`❌ Error: ${message.message}`)
              }
              break
              
            case 'complete':
              onComplete()
              onOutput('✅ Task execution completed')
              break
              
            case 'heartbeat':
              // Just keep the connection alive
              break
              
            default:
              // Unknown message type - ignore
          }
        } catch (error) {
          console.error('Error parsing WebSocket message:', error)
          onError('Error parsing server response')
        }
      }

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
        }
      }

    } catch (error) {
      console.error('Error creating WebSocket:', error)
      onError('Failed to connect to CMBAgent backend')
      setIsConnecting(false)
    }
  }, [onOutput, onResult, onError, onComplete, onStatusChange])

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close(1000, 'User disconnected')
      wsRef.current = null
    }
    setIsConnected(false)
    setIsConnecting(false)
    taskIdRef.current = null
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
    taskId: taskIdRef.current
  }
}
