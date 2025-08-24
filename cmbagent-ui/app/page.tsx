'use client'

import { useState, useCallback, useEffect } from 'react'
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





  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <Header />

      <main className="flex-1 flex flex-col min-h-0">
        {/* Upper 60% - Task Input and Results */}
        <div className="h-[60%] container mx-auto px-4 py-2 min-h-0">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 h-full">
            {/* Left Panel - Task Input */}
            <div className="overflow-y-auto">
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

        {/* Lower 40% - Console Output */}
        <div className="h-[40%] px-4 pb-2 min-h-0">
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
