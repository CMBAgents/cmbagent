'use client'

import { useState, useEffect } from 'react'
import { Play, Settings, Zap, Folder } from 'lucide-react'

interface TaskInputProps {
  onSubmit: (task: string, config: any) => void
  onStop?: () => void
  isRunning: boolean
  isConnecting?: boolean
  onOpenDirectory?: (path: string) => void
}

export default function TaskInput({ onSubmit, onStop, isRunning, isConnecting = false, onOpenDirectory }: TaskInputProps) {
  const [task, setTask] = useState('1 + 1 = ?')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [directoryStatus, setDirectoryStatus] = useState<'unknown' | 'exists' | 'not-exists'>('unknown')
  const [config, setConfig] = useState({
    model: 'gpt-4o',
    maxRounds: 25,
    maxAttempts: 6,
    agent: 'engineer',
    workDir: process.env.HOME ? `${process.env.HOME}/Desktop/cmbdir` : '~/Desktop/cmbdir'
  })

  // Check directory status when workDir changes
  const checkDirectoryStatus = async (path: string) => {
    try {
      const response = await fetch(`/api/files/list?path=${encodeURIComponent(path)}`)
      if (response.ok) {
        setDirectoryStatus('exists')
      } else {
        setDirectoryStatus('not-exists')
      }
    } catch {
      setDirectoryStatus('not-exists')
    }
  }

  // Check directory status on mount and when workDir changes
  useEffect(() => {
    checkDirectoryStatus(config.workDir)
  }, [config.workDir])

  const exampleTasks = [
    '1 + 1 = ?',
    'Plot a sine wave from 0 to 2π',
    'Generate 100 random numbers and plot their histogram',
    'Calculate the factorial of 10',
    'Create a simple linear regression example'
  ]

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (task.trim() && !isRunning) {
      onSubmit(task, config)
    }
  }

  return (
    <div className="bg-white/10 backdrop-blur-sm rounded-xl p-6 border border-white/20">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-white flex items-center">
          <Zap className="w-5 h-5 mr-2 text-yellow-400" />
          Task Input
        </h2>
        <button
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="p-2 text-gray-400 hover:text-white transition-colors"
        >
          <Settings className="w-5 h-5" />
        </button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Working Directory - Always Visible */}
        <div className="p-3 bg-blue-500/10 rounded-lg border border-blue-500/20 mb-4">
          <label className="block text-sm font-medium text-blue-300 mb-2 flex items-center gap-2">
            <Folder className="w-4 h-4" />
            Working Directory
            {directoryStatus === 'exists' && (
              <span className="text-xs bg-green-500/20 text-green-300 px-2 py-1 rounded">
                ✓ Exists
              </span>
            )}
            {directoryStatus === 'not-exists' && (
              <span className="text-xs bg-yellow-500/20 text-yellow-300 px-2 py-1 rounded">
                ⚠ Will be created
              </span>
            )}
            {directoryStatus === 'unknown' && (
              <span className="text-xs bg-gray-500/20 text-gray-300 px-2 py-1 rounded">
                ⋯ Checking
              </span>
            )}
          </label>
          <div className="flex gap-2 mb-2">
            <input
              type="text"
              value={config.workDir}
              onChange={(e) => setConfig({...config, workDir: e.target.value})}
              placeholder="~/Desktop/cmbdir"
              className="flex-1 px-3 py-2 bg-black/30 border border-white/20 rounded-lg text-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              disabled={isRunning}
            />
            <button
              type="button"
              onClick={() => setConfig({...config, workDir: '~/Desktop/cmbdir'})}
              disabled={isRunning}
              className="px-3 py-2 bg-gray-600/20 text-gray-300 rounded-lg text-sm hover:bg-gray-600/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              title="Reset to default"
            >
              Reset
            </button>
            <button
              type="button"
              onClick={() => {
                const newDir = prompt('Enter custom working directory path:', config.workDir)
                if (newDir !== null) {
                  setConfig({...config, workDir: newDir})
                }
              }}
              disabled={isRunning}
              className="px-3 py-2 bg-blue-600/20 text-blue-300 rounded-lg text-sm hover:bg-blue-600/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              title="Browse for directory"
            >
              Browse
            </button>
          </div>
          <div className="flex gap-2 mb-3">
            <button
              type="button"
              onClick={async () => {
                if (confirm('Are you sure you want to clear the working directory? This will remove all task files.')) {
                  try {
                    const response = await fetch(`/api/files/clear-directory?path=${encodeURIComponent(config.workDir)}`, {
                      method: 'DELETE'
                    })

                    if (response.ok) {
                      const result = await response.json()
                      alert(`Successfully cleared directory. ${result.items_deleted} items removed.`)
                    } else {
                      const error = await response.json()
                      alert(`Error clearing directory: ${error.detail}`)
                    }
                  } catch (error) {
                    alert(`Error clearing directory: ${error}`)
                  }
                }
              }}
              disabled={isRunning}
              className="px-3 py-2 bg-red-600/20 text-red-300 rounded-lg text-sm hover:bg-red-600/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Clear Directory
            </button>
            <button
              type="button"
              onClick={() => {
                if (onOpenDirectory) {
                  onOpenDirectory(config.workDir)
                } else {
                  // Fallback to opening in new window
                  window.open(`/api/files/list?path=${encodeURIComponent(config.workDir)}`, '_blank')
                }
              }}
              disabled={isRunning}
              className="px-3 py-2 bg-green-600/20 text-green-300 rounded-lg text-sm hover:bg-green-600/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Open Directory
            </button>


          </div>
          <p className="text-xs text-blue-200/70">
            Directory where CMBAgent will save outputs (chats, codebase, plots, etc.)
          </p>
        </div>

        {/* Task Input */}
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1">
            Task Description
          </label>
          <textarea
            value={task}
            onChange={(e) => setTask(e.target.value)}
            placeholder="Enter your task here..."
            className="w-full h-20 px-3 py-2 bg-black/30 border border-white/20 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none text-sm"
            disabled={isRunning}
          />
        </div>

        {/* Example Tasks */}
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1">
            Quick Examples
          </label>
          <div className="flex flex-wrap gap-1">
            {exampleTasks.map((example, index) => (
              <button
                key={index}
                type="button"
                onClick={() => setTask(example)}
                disabled={isRunning}
                className="px-2 py-1 text-xs bg-blue-600/20 text-blue-300 rounded hover:bg-blue-600/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {example}
              </button>
            ))}
          </div>
        </div>

        {/* Advanced Configuration */}
        {showAdvanced && (
          <div className="space-y-3 p-3 bg-black/20 rounded-lg border border-white/10">
            <h3 className="text-sm font-medium text-gray-300">Advanced Configuration</h3>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-gray-400 mb-1">Model</label>
                <select
                  value={config.model}
                  onChange={(e) => setConfig({...config, model: e.target.value})}
                  className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                  disabled={isRunning}
                >
                  <option value="gpt-4o">GPT-4o</option>
                  <option value="gpt-4o-mini">GPT-4o Mini</option>
                  <option value="gpt-4">GPT-4</option>
                </select>
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1">Agent</label>
                <select
                  value={config.agent}
                  onChange={(e) => setConfig({...config, agent: e.target.value})}
                  className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                  disabled={isRunning}
                >
                  <option value="engineer">Engineer</option>
                  <option value="researcher">Researcher</option>
                </select>
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1">Max Rounds</label>
                <input
                  type="number"
                  value={config.maxRounds}
                  onChange={(e) => setConfig({...config, maxRounds: parseInt(e.target.value)})}
                  min="1"
                  max="100"
                  className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                  disabled={isRunning}
                />
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1">Max Attempts</label>
                <input
                  type="number"
                  value={config.maxAttempts}
                  onChange={(e) => setConfig({...config, maxAttempts: parseInt(e.target.value)})}
                  min="1"
                  max="20"
                  className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                  disabled={isRunning}
                />
              </div>
            </div>
          </div>
        )}

        {/* Submit/Stop Button */}
        <div className="flex space-x-3">
          <button
            type="submit"
            disabled={!task.trim() || isRunning || isConnecting}
            className="flex-1 flex items-center justify-center px-6 py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors"
          >
            {isConnecting ? (
              <>
                <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-white mr-2"></div>
                Connecting...
              </>
            ) : isRunning ? (
              <>
                <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-white mr-2"></div>
                Running...
              </>
            ) : (
              <>
                <Play className="w-5 h-5 mr-2" />
                Execute Task
              </>
            )}
          </button>

          {(isRunning || isConnecting) && onStop && (
            <button
              type="button"
              onClick={onStop}
              className="px-6 py-3 bg-red-600 hover:bg-red-700 text-white font-medium rounded-lg transition-colors"
            >
              Stop
            </button>
          )}
        </div>
      </form>
    </div>
  )
}
