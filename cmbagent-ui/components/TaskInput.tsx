'use client'

import { useState } from 'react'
import { Play, Settings, Zap, Folder } from 'lucide-react'

interface TaskInputProps {
  onSubmit: (task: string, config: any) => void
  onStop?: () => void
  isRunning: boolean
  isConnecting?: boolean
  onOpenDirectory?: (path: string) => void
}

export default function TaskInput({ onSubmit, onStop, isRunning, isConnecting = false, onOpenDirectory }: TaskInputProps) {
  const [task, setTask] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [showWorkDir, setShowWorkDir] = useState(false)
  const [mode, setMode] = useState<'one-shot' | 'planning-control'>('one-shot')

  const [config, setConfig] = useState({
    model: 'gpt-4.1-2025-04-14',
    maxRounds: 25,
    maxAttempts: 6,
    agent: 'engineer',
    workDir: '~/Desktop/cmbdir',
    mode: 'one-shot' as 'one-shot' | 'planning-control',
    // Planning & Control specific options
    maxPlanSteps: 2,
    nPlanReviews: 1,
    planInstructions: '',
    plannerModel: 'gpt-4.1-2025-04-14',
    researcherModel: 'gpt-4.1-2025-04-14', 
    planReviewerModel: 'o3-mini-2025-01-31'
  })



  const exampleTasks = [
    'Plot a 3D Möbius strip using matplotlib.',
    'Plot a sine wave from 0 to 2π',
    'Generate 100 random numbers and plot their histogram'
  ]

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (task.trim() && !isRunning) {
      onSubmit(task, { ...config, mode })
    }
  }

  return (
    <div className="bg-white/10 backdrop-blur-sm rounded-xl p-4 border border-white/20">
      {/* Mode Selection Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center space-x-1">
          <button
            onClick={() => {
              setMode('one-shot')
              setConfig(prev => ({ ...prev, mode: 'one-shot' }))
            }}
            disabled={isRunning}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
              mode === 'one-shot'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'bg-black/30 text-gray-300 hover:text-white hover:bg-black/50'
            } disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            <Zap className="w-3 h-3 mr-1 inline" />
            One Shot
          </button>
          <button
            onClick={() => {
              setMode('planning-control')
              setConfig(prev => ({ ...prev, mode: 'planning-control' }))
            }}
            disabled={isRunning}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
              mode === 'planning-control'
                ? 'bg-purple-600 text-white shadow-sm'
                : 'bg-black/30 text-gray-300 hover:text-white hover:bg-black/50'
            } disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            📋 Planning & Control
          </button>
        </div>
        <button
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="p-1 text-gray-400 hover:text-white transition-colors"
        >
          <Settings className="w-4 h-4" />
        </button>
      </div>

      {/* Mode Description */}
      <div className="mb-3 p-2 bg-black/10 rounded-lg border border-white/5">
        <p className="text-xs text-gray-300">
          {mode === 'one-shot' ? (
            <>
              <span className="font-medium text-blue-300">One Shot:</span> Direct execution - CMBAgent executes your task immediately without planning
            </>
          ) : (
            <>
              <span className="font-medium text-purple-300">Planning & Control:</span> Task is broken into steps by a planner, then executed step-by-step
            </>
          )}
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-3">


        {/* Task Input */}
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1">
            Task Description
          </label>
          <textarea
            value={task}
            onChange={(e) => setTask(e.target.value)}
            placeholder="Describe the task here..."
            className="w-full h-28 px-3 py-2 bg-black/30 border border-white/20 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none text-sm"
            disabled={isRunning}
          />
        </div>

        {/* Example Tasks */}
        <div>
          <label className="block text-xs font-medium text-gray-300 mb-1">
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
          <div className="space-y-2 p-2 bg-black/20 rounded-lg border border-white/10">
            <h3 className="text-xs font-medium text-gray-300">
              Advanced Configuration - {mode === 'one-shot' ? 'One Shot' : 'Planning & Control'} Mode
            </h3>

            <div className="grid grid-cols-2 gap-3">
              {/* Planning & Control Agent Models - Top Priority */}
              {mode === 'planning-control' ? (
                <>
                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Planner</label>
                    <select
                      value={config.plannerModel || 'gpt-4.1-2025-04-14'}
                      onChange={(e) => setConfig({...config, plannerModel: e.target.value})}
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    >
                      <option value="gpt-4o">GPT-4o</option>
                      <option value="gpt-4o-mini">GPT-4o Mini</option>
                      <option value="gpt-4.1-2025-04-14">GPT-4.1</option>
                      <option value="gpt-5-2025-08-07">GPT-5</option>
                      <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
                      <option value="claude-sonnet-4-20250514">Claude Sonnet 4</option>
                      <option value="o3-mini-2025-01-31">o3-mini</option>
                    </select>
                  </div>

                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Plan Reviewer</label>
                    <select
                      value={config.planReviewerModel || 'o3-mini-2025-01-31'}
                      onChange={(e) => setConfig({...config, planReviewerModel: e.target.value})}
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    >
                      <option value="gpt-4o">GPT-4o</option>
                      <option value="gpt-4o-mini">GPT-4o Mini</option>
                      <option value="gpt-4.1-2025-04-14">GPT-4.1</option>
                      <option value="gpt-5-2025-08-07">GPT-5</option>
                      <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
                      <option value="claude-sonnet-4-20250514">Claude Sonnet 4</option>
                      <option value="o3-mini-2025-01-31">o3-mini</option>
                    </select>
                  </div>

                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Engineer</label>
                    <select
                      value={config.model}
                      onChange={(e) => setConfig({...config, model: e.target.value})}
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    >
                      <option value="gpt-4o">GPT-4o</option>
                      <option value="gpt-4o-mini">GPT-4o Mini</option>
                      <option value="gpt-4.1-2025-04-14">GPT-4.1</option>
                      <option value="gpt-5-2025-08-07">GPT-5</option>
                      <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
                      <option value="claude-sonnet-4-20250514">Claude Sonnet 4</option>
                      <option value="o3-mini-2025-01-31">o3-mini</option>
                    </select>
                  </div>

                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Researcher</label>
                    <select
                      value={config.researcherModel || 'gpt-4.1-2025-04-14'}
                      onChange={(e) => setConfig({...config, researcherModel: e.target.value})}
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    >
                      <option value="gpt-4o">GPT-4o</option>
                      <option value="gpt-4o-mini">GPT-4o Mini</option>
                      <option value="gpt-4.1-2025-04-14">GPT-4.1</option>
                      <option value="gpt-5-2025-08-07">GPT-5</option>
                      <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
                      <option value="claude-sonnet-4-20250514">Claude Sonnet 4</option>
                      <option value="o3-mini-2025-01-31">o3-mini</option>
                    </select>
                  </div>
                </>
              ) : (
                /* One Shot Model Selection */
                <>
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
                      <option value="gpt-4.1-2025-04-14">GPT-4.1</option>
                      <option value="gpt-5-2025-08-07">GPT-5</option>
                      <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
                      <option value="claude-sonnet-4-20250514">Claude Sonnet 4</option>
                      <option value="o3-mini-2025-01-31">o3-mini</option>
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
                </>
              )}

              <div>
                <label className="block text-xs text-gray-400 mb-1">
                  {mode === 'planning-control' ? 'Max Control Rounds' : 'Max Rounds'}
                </label>
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

              {/* Additional Planning & Control Options */}
              {mode === 'planning-control' && (
                <>

                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Max Plan Steps</label>
                    <input
                      type="number"
                      value={config.maxPlanSteps || 2}
                      onChange={(e) => setConfig({...config, maxPlanSteps: parseInt(e.target.value)})}
                      min="1"
                      max="10"
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    />
                  </div>

                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Plan Reviews</label>
                    <input
                      type="number"
                      value={config.nPlanReviews || 1}
                      onChange={(e) => setConfig({...config, nPlanReviews: parseInt(e.target.value)})}
                      min="0"
                      max="5"
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    />
                  </div>

                  <div className="col-span-2">
                    <label className="block text-xs text-gray-400 mb-1">Plan Instructions (Optional)</label>
                    <textarea
                      value={config.planInstructions || ''}
                      onChange={(e) => setConfig({...config, planInstructions: e.target.value})}
                      placeholder="e.g., Use engineer for whole analysis. Plan must have 2 steps."
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
                      rows={2}
                      disabled={isRunning}
                    />
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {/* Working Directory - Collapsible */}
        <div className="border border-gray-500/20 rounded">
          <button
            type="button"
            onClick={() => setShowWorkDir(!showWorkDir)}
            className="w-full flex items-center justify-between p-2 text-xs text-gray-400 hover:text-gray-300 transition-colors"
          >
            <div className="flex items-center gap-2">
              <Folder className="w-3 h-3" />
              <span>Working Directory: {config.workDir}</span>
            </div>
            <span className="text-xs">{showWorkDir ? '▼' : '▶'}</span>
          </button>

          {showWorkDir && (
            <div className="p-2 border-t border-gray-500/20 bg-gray-500/5">
              <div className="space-y-1">
                <div className="flex gap-1">
                  <input
                    type="text"
                    value={config.workDir}
                    onChange={(e) => setConfig({...config, workDir: e.target.value})}
                    placeholder="~/Desktop/cmbdir"
                    className="flex-1 px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-transparent"
                    disabled={isRunning}
                  />
                  <button
                    type="button"
                    onClick={() => setConfig({...config, workDir: '~/Desktop/cmbdir'})}
                    disabled={isRunning}
                    className="px-2 py-1 bg-gray-600/20 text-gray-300 rounded text-xs hover:bg-gray-600/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    title="Reset to default"
                  >
                    Reset
                  </button>
                </div>
                <div className="flex gap-1">
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
                    className="px-2 py-1 bg-red-600/20 text-red-300 rounded text-xs hover:bg-red-600/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    title="Clear all files in directory"
                  >
                    Clear Directory
                  </button>
                  {onOpenDirectory && (
                    <button
                      type="button"
                      onClick={() => onOpenDirectory(config.workDir)}
                      disabled={isRunning}
                      className="px-2 py-1 bg-green-600/20 text-green-300 rounded text-xs hover:bg-green-600/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                      title="Open directory"
                    >
                      Open Directory
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Submit/Stop Button */}
        <div className="flex space-x-2">
          <button
            type="submit"
            disabled={!task.trim() || isRunning || isConnecting}
            className="flex-1 flex items-center justify-center px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors text-sm"
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
                <Play className="w-4 h-4 mr-2" />
                Submit Task
              </>
            )}
          </button>

          {(isRunning || isConnecting) && onStop && (
            <button
              type="button"
              onClick={onStop}
              className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white font-medium rounded-lg transition-colors text-sm"
            >
              Stop
            </button>
          )}
        </div>
      </form>
    </div>
  )
}
