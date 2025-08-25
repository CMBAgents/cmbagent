'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import TaskInput from '@/components/TaskInput'
import ConsoleOutput from '@/components/ConsoleOutput'
import ResultDisplay from '@/components/ResultDisplay'
import Header from '@/components/Header'
import { useWebSocket } from '@/hooks/useWebSocket'

export default function Home() {
  const [isRunning, setIsRunning] = useState(false)
  const [consoleOutput, setConsoleOutput] = useState<string[]>([])
  const [results, setResults] = useState<any>(null)
  const [status, setStatus] = useState<string>('Ready')
  const [directoryToOpen, setDirectoryToOpen] = useState<string | null>(null)
  const [upperHeight, setUpperHeight] = useState(60) // Percentage for upper section
  const [isResizing, setIsResizing] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const addConsoleOutput = useCallback((output: string) => {
    setConsoleOutput(prev => [...prev, output])
  }, [])

  const handleResult = useCallback((result: any) => {
    setResults(result)
  }, [])

  const handleError = useCallback((error: string) => {
    addConsoleOutput(`❌ Error: ${error}`)
    setIsRunning(false)
  }, [addConsoleOutput])

  const handleComplete = useCallback(() => {
    setIsRunning(false)
    setStatus('Task completed')
  }, [])

  const handleStatusChange = useCallback((newStatus: string) => {
    setStatus(newStatus)
  }, [])

  const { connect, disconnect, isConnected, isConnecting } = useWebSocket({
    onOutput: addConsoleOutput,
    onResult: handleResult,
    onError: handleError,
    onComplete: handleComplete,
    onStatusChange: handleStatusChange
  })

  const handleTaskSubmit = async (task: string, config: any) => {
    if (isRunning) return

    setIsRunning(true)
    setConsoleOutput([])
    setResults(null)
    setStatus('Connecting...')

    // Generate a unique task ID
    const taskId = `task_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`

    try {
      await connect(taskId, task, config)
    } catch (error) {
      console.error('Failed to start task:', error)
      handleError('Failed to connect to backend')
    }
  }

  const handleStopTask = () => {
    disconnect()
    setIsRunning(false)
    setStatus('Task stopped')
    addConsoleOutput('🛑 Task execution stopped by user')
  }

  const clearConsole = () => {
    setConsoleOutput([])
  }

  const handleOpenDirectory = useCallback((path: string) => {
    setDirectoryToOpen(path)
    // Create a mock results object to show the file browser
    const mockResults = {
      execution_time: 0,
      base_work_dir: path,
      work_dir: path
    }
    setResults(mockResults)
  }, [])

  // Handle resizing
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    setIsResizing(true)
    e.preventDefault()
  }, [])

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!isResizing || !containerRef.current) return

    const containerRect = containerRef.current.getBoundingClientRect()
    const containerHeight = containerRect.height
    const mouseY = e.clientY - containerRect.top

    // Calculate percentage (min 20%, max 80%)
    const newUpperHeight = Math.min(80, Math.max(20, (mouseY / containerHeight) * 100))
    setUpperHeight(newUpperHeight)
  }, [isResizing])

  const handleMouseUp = useCallback(() => {
    setIsResizing(false)
  }, [])

  useEffect(() => {
    if (isResizing) {
      document.addEventListener('mousemove', handleMouseMove)
      document.addEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = 'ns-resize'
      document.body.style.userSelect = 'none'
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [isResizing, handleMouseMove, handleMouseUp])





  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <Header />

      <main ref={containerRef} className="flex-1 flex flex-col min-h-0">
        {/* Upper Section - Task Input and Results */}
        <div 
          className="container mx-auto px-4 py-2 min-h-0 overflow-hidden"
          style={{ height: `${upperHeight}%` }}
        >
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 h-full">
            {/* Left Panel - Task Input */}
            <div className="h-full overflow-y-auto">
              <TaskInput
                onSubmit={handleTaskSubmit}
                onStop={handleStopTask}
                isRunning={isRunning}
                isConnecting={isConnecting}
                onOpenDirectory={handleOpenDirectory}
              />
            </div>

            {/* Right Panel - Results */}
            <div className="h-full overflow-y-auto">
              <ResultDisplay results={results} />
            </div>
          </div>
        </div>

        {/* Resizer Handle */}
        <div
          className={`h-1.5 bg-gray-600/30 hover:bg-blue-500/50 cursor-ns-resize transition-all duration-200 relative group flex-shrink-0 ${isResizing ? 'bg-blue-500/70' : ''}`}
          onMouseDown={handleMouseDown}
          title="Drag to resize console height"
        >
          <div className="absolute inset-x-0 -top-2 -bottom-2 flex items-center justify-center">
            <div className={`w-16 h-0.5 bg-gray-500/50 rounded-full group-hover:bg-blue-400/70 group-hover:w-20 transition-all duration-200 ${isResizing ? 'bg-blue-400 w-20' : ''}`}></div>
          </div>
          {/* Add dots for better visual indication */}
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="flex space-x-0.5">
              <div className={`w-1 h-1 bg-gray-400/40 rounded-full transition-opacity duration-200 ${isResizing ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}></div>
              <div className={`w-1 h-1 bg-gray-400/40 rounded-full transition-opacity duration-200 ${isResizing ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}></div>
              <div className={`w-1 h-1 bg-gray-400/40 rounded-full transition-opacity duration-200 ${isResizing ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}></div>
            </div>
          </div>
        </div>

        {/* Lower Section - Console Output */}
        <div 
          className="px-4 pb-2 min-h-0 overflow-hidden"
          style={{ height: `${100 - upperHeight}%` }}
        >
          <ConsoleOutput
            output={consoleOutput}
            isRunning={isRunning}
            onClear={clearConsole}
          />
        </div>
      </main>
    </div>
  )
}
