'use client'

import { useState } from 'react'
import { Play, Settings, Zap, Folder, HelpCircle } from 'lucide-react'

// Tooltip component - tooltip appears only when hovering over the question mark icon
const Tooltip = ({ children, text, wide = false, position = 'auto' }: { children: React.ReactNode; text?: string; wide?: boolean; position?: 'auto' | 'top' | 'bottom' }) => (
  <div className="inline-flex items-center gap-1">
    {children}
    <div className="relative group">
      <HelpCircle className="w-3 h-3 text-gray-500 hover:text-gray-300 cursor-help" />
      {text && (
        <span className={`absolute ${position === 'bottom' ? 'top-full mt-2' : 'bottom-full mb-2'} left-0 px-3 py-2 text-xs text-white bg-gray-900 rounded-lg shadow-lg opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none z-50 ${wide ? 'whitespace-normal w-80' : 'whitespace-nowrap'}`}>
          {text}
        </span>
      )}
    </div>
  </div>
)

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
  const [mode, setMode] = useState<'one-shot' | 'planning-control' | 'idea-generation'>('one-shot')

  const [config, setConfig] = useState({
    model: 'gpt-4.1-2025-04-14',
    maxRounds: 25,
    maxAttempts: 6,
    agent: 'engineer',
    workDir: '~/Desktop/cmbdir',
    mode: 'one-shot' as 'one-shot' | 'planning-control' | 'idea-generation',
    // Planning & Control specific options
    maxPlanSteps: 6,
    nPlanReviews: 1,
    planInstructions: '',
    plannerModel: 'gpt-4.1-2025-04-14',
    researcherModel: 'gpt-4.1-2025-04-14', 
    planReviewerModel: 'o3-mini-2025-01-31',
    // Idea Generation specific options
    ideaMakerModel: 'gpt-4.1-2025-04-14',
    ideaHaterModel: 'o3-mini-2025-01-31'
  })

  // Default plan instructions for different modes
  const getDefaultPlanInstructions = (mode: string) => {
    if (mode === 'idea-generation') {
      return `Given these datasets, and information, make a plan according to the following instructions: 

- Ask idea_maker to generate 5 new research project ideas related to the datasets.
- Ask idea_hater to critique these ideas.
- Ask idea_maker to select and improve 2 out of the 5 research project ideas given the output of the idea_hater.
- Ask idea_hater to critique the 2 improved ideas. 
- Ask idea_maker to select the best idea out of the 2. 
- Ask idea_maker to report the best idea in the form of a scientific paper title with a 5-sentence description. 

The goal of this task is to generate a research project idea based on the data of interest. 
Don't suggest to perform any calculations or analyses here. The only goal of this task is to obtain the best possible project idea.`
    } else if (mode === 'planning-control') {
      return 'Use engineer for the the whole analaysis.'
    }
    return ''
  }



  const getExampleTasks = (mode: string) => {
    if (mode === 'idea-generation') {
      return [
        'Bank customer data during covid-19',
        'Galaxy cluster observations from Hubble telescope', 
        'Climate change temperature records 1900-2020'
      ]
    }
    return [
      'Plot a 3D Möbius strip using matplotlib.',
      'Simulate stock prices during Trump tariffs',
      'Generate 100 random numbers and plot their histogram'
    ]
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (task.trim() && !isRunning) {
      onSubmit(task, { ...config, mode })
    }
  }

  return (
    <div className="h-full bg-white/10 backdrop-blur-sm rounded-xl p-4 border border-white/20 flex flex-col">
      {/* Mode Selection Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center space-x-1">
          <Tooltip text="Direct execution - CMBAgent executes your task immediately without planning" position="bottom">
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
          </Tooltip>
          <Tooltip text="Task is broken into steps by a planner, then executed step-by-step" position="bottom">
            <button
              onClick={() => {
                setMode('planning-control')
                setConfig(prev => ({ 
                  ...prev, 
                  mode: 'planning-control',
                  planInstructions: getDefaultPlanInstructions('planning-control')
                }))
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
          </Tooltip>
          <Tooltip text="Generate research ideas using idea maker and idea hater agents in iterative workflow" wide position="bottom">
            <button
              onClick={() => {
                setMode('idea-generation')
                setConfig(prev => ({ 
                  ...prev, 
                  mode: 'idea-generation',
                  planInstructions: getDefaultPlanInstructions('idea-generation')
                }))
              }}
              disabled={isRunning}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                mode === 'idea-generation'
                  ? 'bg-green-600 text-white shadow-sm'
                  : 'bg-black/30 text-gray-300 hover:text-white hover:bg-black/50'
              } disabled:opacity-50 disabled:cursor-not-allowed`}
            >
              💡 Idea Generation
            </button>
          </Tooltip>
        </div>
        <button
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="p-1 text-gray-400 hover:text-white transition-colors"
        >
          <Settings className="w-4 h-4" />
        </button>
      </div>

      <form onSubmit={handleSubmit} className="flex-1 flex flex-col space-y-3">


        {/* Task Input */}
        <div>
          <textarea
            value={task}
            onChange={(e) => setTask(e.target.value)}
            placeholder={mode === 'idea-generation' ? 
              "Describe dataset or problem of interest..." : 
              "Describe the task here..."
            }
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
            {getExampleTasks(mode).map((example, index) => (
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
              Advanced Configuration - {mode === 'one-shot' ? 'One Shot' : mode === 'planning-control' ? 'Planning & Control' : 'Idea Generation'} Mode
            </h3>

            <div className="grid grid-cols-2 gap-3">
              {/* Idea Generation Agent Models */}
              {mode === 'idea-generation' ? (
                <>
                  <div>
                    <Tooltip text="Agent that generates creative research project ideas" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Idea Maker</label>
                    </Tooltip>
                    <select
                      value={config.ideaMakerModel || 'gpt-4.1-2025-04-14'}
                      onChange={(e) => setConfig({...config, ideaMakerModel: e.target.value})}
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
                    <Tooltip text="Agent that critically evaluates and provides feedback on ideas" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Idea Hater</label>
                    </Tooltip>
                    <select
                      value={config.ideaHaterModel || 'o3-mini-2025-01-31'}
                      onChange={(e) => setConfig({...config, ideaHaterModel: e.target.value})}
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
                    <Tooltip text="Agent that breaks down tasks into manageable steps and creates execution plans" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Planner</label>
                    </Tooltip>
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
                    <Tooltip text="Agent that reviews and improves execution plans before implementation" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Plan Reviewer</label>
                    </Tooltip>
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
                </>
              ) : /* Planning & Control Agent Models - Top Priority */
              mode === 'planning-control' ? (
                <>
                  <div>
                    <Tooltip text="Agent that breaks down tasks into manageable steps and creates execution plans" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Planner</label>
                    </Tooltip>
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
                    <Tooltip text="Agent that reviews and improves execution plans before implementation" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Plan Reviewer</label>
                    </Tooltip>
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
                    <Tooltip text="Agent that handles technical implementation, coding, and data analysis tasks" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Engineer</label>
                    </Tooltip>
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
                    <Tooltip text="Agent that provides detailed reasoning, analysis, and comprehensive reports" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Researcher</label>
                    </Tooltip>
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
                    <Tooltip text="AI model used for task execution" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Model</label>
                    </Tooltip>
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
                    <Tooltip text="Type of specialized agent to handle the task (engineer for coding, researcher for analysis)" wide position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Agent</label>
                    </Tooltip>
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
                <Tooltip text="Maximum number of conversation rounds between agents before stopping" position="bottom">
                  <label className="block text-xs text-gray-400 mb-1">
                    {mode === 'planning-control' ? 'Max Control Rounds' : 'Max Rounds'}
                  </label>
                </Tooltip>
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
                <Tooltip text="Maximum number of retry attempts when tasks fail or encounter errors" position="bottom">
                  <label className="block text-xs text-gray-400 mb-1">Max Attempts</label>
                </Tooltip>
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

              {/* Additional Planning & Control and Idea Generation Options */}
              {(mode === 'planning-control' || mode === 'idea-generation') && (
                <>

                  <div>
                    <Tooltip text="Maximum number of steps the planner can break the task into" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Max Plan Steps</label>
                    </Tooltip>
                    <input
                      type="number"
                      value={config.maxPlanSteps || (mode === 'idea-generation' ? 6 : 2)}
                      onChange={(e) => setConfig({...config, maxPlanSteps: parseInt(e.target.value)})}
                      min="1"
                      max="10"
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    />
                  </div>

                  <div>
                    <Tooltip text="Number of times the plan will be reviewed and refined before execution" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Plan Reviews</label>
                    </Tooltip>
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
                    <Tooltip text="Specific instructions to guide the planner on how to approach the task and which agents to use" wide>
                      <label className="block text-xs text-gray-400 mb-1">Plan Instructions</label>
                    </Tooltip>
                    <textarea
                      value={config.planInstructions || getDefaultPlanInstructions(mode)}
                      onChange={(e) => setConfig({...config, planInstructions: e.target.value})}
                      placeholder={mode === 'idea-generation' ? 
                        "Default instructions loaded for idea generation workflow" : 
                        mode === 'planning-control' ?
                        "Default instruction loaded for planning & control workflow" :
                        "Enter plan instructions here"
                      }
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
                      rows={mode === 'idea-generation' ? 12 : 2}
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
