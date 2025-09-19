'use client'

import { useState, useEffect, useRef } from 'react'
import { Play, Settings, Zap, HelpCircle, ChevronDown } from 'lucide-react'
import { CredentialsKeyIcon } from './CredentialsKeyIcon'
import { CredentialsModal } from './CredentialsModal'
import { ModelSelector } from './ModelSelector'
import { useCredentials } from '../hooks/useCredentials'

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
  const [mode, setMode] = useState<'one-shot' | 'planning-control' | 'idea-generation' | 'ocr' | 'arxiv' | 'enhance-input'>('one-shot')
  const [showOcrDropdown, setShowOcrDropdown] = useState(false)
  const [showCredentialsModal, setShowCredentialsModal] = useState(false)
  const [showOpenAIError, setShowOpenAIError] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)
  
  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowOcrDropdown(false)
      }
    }
    
    document.addEventListener('mousedown', handleClickOutside)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [])
  
  // Use credentials hook
  const { 
    refreshKey, 
    handleStatusChange, 
    refreshCredentials, 
    getValidation, 
    getAvailableModels,
    isModelAvailable,
    credentialStatus 
  } = useCredentials()

  const [config, setConfig] = useState({
    model: 'gpt-4.1-2025-04-14',
    maxRounds: 25,
    maxAttempts: 1,
    agent: 'engineer',
    workDir: '~/cmbagent_workdir',
    mode: 'one-shot' as 'one-shot' | 'planning-control' | 'idea-generation' | 'ocr' | 'arxiv' | 'enhance-input',
    // Global model options
    defaultModel: 'gpt-4.1-2025-04-14',
    defaultFormatterModel: 'o3-mini-2025-01-31',
    // Planning & Control specific options
    maxPlanSteps: 2,
    nPlanReviews: 1,
    planInstructions: '',
    plannerModel: 'gpt-4.1-2025-04-14',
    researcherModel: 'gpt-4.1-2025-04-14', 
    planReviewerModel: 'o3-mini-2025-01-31',
    // Idea Generation specific options
    ideaMakerModel: 'gpt-4.1-2025-04-14',
    ideaHaterModel: 'o3-mini-2025-01-31',
    // OCR specific options
    saveMarkdown: true,
    saveJson: true,
    saveText: false,
    maxWorkers: 4,
    maxDepth: 10,
    ocrOutputDir: ''
  })

  // Default plan instructions for different modes
  const getDefaultPlanInstructions = (mode: string) => {
    if (mode === 'idea-generation') {
      return `Given these datasets, and information, make a plan in 6 steps according to the following instructions: 

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
    } else if (mode === 'ocr') {
      return [
        '/Users/username/Documents/research_papers/',
        '/Users/username/Desktop/scientific_paper.pdf',
        '~/Downloads/reports/'
      ]
    } else if (mode === 'arxiv') {
      return [
        'Fuzzy dark matter models at https://arxiv.org/pdf/1610.08297',
        'Recent JWST observations show Little Red Dots at https://arxiv.org/abs/2509.02664',
        'Black hole mass measurements https://arxiv.org/html/2508.21748'
      ]
    } else if (mode === 'enhance-input') {
      return [
        'We want to develop a comprehensive market impact model building on recent advances in square-root price impact. The work in https://arxiv.org/abs/2509.05065 shows how order imbalance and volatility interact in artificial markets.',
        'Recent cosmic birefringence measurements from ACT are fascinating. The analysis in https://arxiv.org/pdf/2509.13654 provides new constraints on axion-like particles and cosmic magnetic fields.',
        'what is the link between that paper on surface diffusion for neurodevelopment https://arxiv.org/pdf/2508.03706 and this one on conditional control in text-to-image diffusion https://arxiv.org/pdf/2302.05543'
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
    
    const validation = getValidation()
    console.log('Validation result:', validation) // Debug log
    if (!validation.canSubmitTask) {
      // OpenAI missing - show error in main UI (red key scenario)
      console.log('Showing OpenAI error - canSubmitTask is false')
      setShowOpenAIError(true)
      // Auto-hide after 5 seconds
      setTimeout(() => setShowOpenAIError(false), 5000)
      return
    }
    
    // Check if selected model is available
    if (!isModelAvailable(config.model)) {
      const availableModels = getAvailableModels()
      const fallbackModel = availableModels.openai[0] || 'gpt-4.1-2025-04-14'
      alert(`Selected model "${config.model}" requires additional credentials. Switching to "${fallbackModel}".`)
      setConfig(prev => ({ ...prev, model: fallbackModel }))
      return
    }
    
    if (task.trim() && !isRunning) {
      onSubmit(task, { ...config, mode })
    }
  }

  return (
    <div className="h-full flex flex-col">
      {/* Mode Selection Header */}
      <div className="flex items-center justify-between mb-3 px-4 pt-4">
        <div className="flex items-center space-x-1">
          <Tooltip text="Task is broken into steps by a planner, then executed step-by-step" position="bottom">
            <button
              onClick={() => {
                setMode('planning-control')
                setConfig(prev => ({ 
                  ...prev, 
                  mode: 'planning-control',
                  maxPlanSteps: 2,
                  planInstructions: getDefaultPlanInstructions('planning-control')
                }))
              }}
              disabled={isRunning}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                mode === 'planning-control'
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'bg-black/30 text-gray-300 hover:text-white hover:bg-black/50'
              } disabled:opacity-50 disabled:cursor-not-allowed`}
            >
              📋 Deep Research
            </button>
          </Tooltip>
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
          <Tooltip text="Generate research ideas using idea maker and idea hater agents in iterative workflow" wide position="bottom">
            <button
              onClick={() => {
                setMode('idea-generation')
                setConfig(prev => ({ 
                  ...prev, 
                  mode: 'idea-generation',
                  maxPlanSteps: 6,
                  planInstructions: getDefaultPlanInstructions('idea-generation')
                }))
              }}
              disabled={isRunning}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                mode === 'idea-generation'
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'bg-black/30 text-gray-300 hover:text-white hover:bg-black/50'
              } disabled:opacity-50 disabled:cursor-not-allowed`}
            >
              💡 Idea Generation
            </button>
          </Tooltip>
          {/* More Tools Dropdown */}
          <div className="relative" ref={dropdownRef}>
            <Tooltip text={
              mode === 'arxiv' ? "Filter text for arXiv URLs and download papers" :
              mode === 'ocr' ? "Process PDFs with OCR - Extract text from PDF files or folders containing PDFs" :
              mode === 'enhance-input' ? "Enhance input text with contextual information from referenced arXiv papers" :
              "Additional tools for document processing and content filtering"
            } wide position="bottom">
              <button
                onClick={() => setShowOcrDropdown(!showOcrDropdown)}
                disabled={isRunning}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all flex items-center gap-1 ${
                  mode === 'ocr' || mode === 'arxiv' || mode === 'enhance-input'
                    ? 'bg-blue-600 text-white shadow-sm'
                    : 'bg-black/30 text-gray-300 hover:text-white hover:bg-black/50'
                } disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                {mode === 'arxiv' ? '📚 arXiv Filter' : mode === 'ocr' ? '📄 PDF OCR' : mode === 'enhance-input' ? '✨ Enhance Input' : '🔧 More Tools'}
                <ChevronDown className="w-3 h-3" />
              </button>
            </Tooltip>
            
            {/* Dropdown Menu */}
            {showOcrDropdown && (
              <div className="absolute top-full left-0 mt-1 bg-gray-800 border border-white/20 rounded-lg shadow-lg z-50 min-w-[160px]">
                <button
                  onClick={() => {
                    setMode('arxiv')
                    setConfig(prev => ({ ...prev, mode: 'arxiv' }))
                    setShowOcrDropdown(false)
                  }}
                  disabled={isRunning}
                  className={`w-full px-3 py-2 text-left text-sm transition-colors rounded-t-lg ${
                    mode === 'arxiv'
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-300 hover:text-white hover:bg-gray-700'
                  } disabled:opacity-50 disabled:cursor-not-allowed`}
                >
                  📚 arXiv Filter
                </button>
                <button
                  onClick={() => {
                    setMode('ocr')
                    setConfig(prev => ({ ...prev, mode: 'ocr' }))
                    setShowOcrDropdown(false)
                  }}
                  disabled={isRunning}
                  className={`w-full px-3 py-2 text-left text-sm transition-colors ${
                    mode === 'ocr'
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-300 hover:text-white hover:bg-gray-700'
                  } disabled:opacity-50 disabled:cursor-not-allowed`}
                >
                  📄 PDF OCR
                </button>
                <button
                  onClick={() => {
                    setMode('enhance-input')
                    setConfig(prev => ({ ...prev, mode: 'enhance-input' }))
                    setShowOcrDropdown(false)
                  }}
                  disabled={isRunning}
                  className={`w-full px-3 py-2 text-left text-sm transition-colors rounded-b-lg ${
                    mode === 'enhance-input'
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-300 hover:text-white hover:bg-gray-700'
                  } disabled:opacity-50 disabled:cursor-not-allowed`}
                >
                  ✨ Enhance Input
                </button>
              </div>
            )}
          </div>
        </div>
        <button
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="p-1 text-gray-400 hover:text-white transition-colors"
        >
          <Settings className="w-4 h-4" />
        </button>
        <CredentialsKeyIcon
          refreshKey={refreshKey}
          onOpenCredentialsModal={() => setShowCredentialsModal(true)}
          onStatusChange={handleStatusChange}
        />
      </div>

      <form onSubmit={handleSubmit} className="flex-1 flex flex-col space-y-3 px-4 pb-4">


        {/* Task Input */}
        <div>
          {mode === 'ocr' ? (
            /* Single-line input for OCR mode */
            <input
              type="text"
              value={task}
              onChange={(e) => setTask(e.target.value)}
              placeholder="Enter path to PDF file or folder containing PDFs..."
              className="w-full px-3 py-2 bg-black/30 border border-white/20 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
              disabled={isRunning}
            />
          ) : mode === 'arxiv' ? (
            /* Multi-line textarea for arXiv mode */
            <textarea
              value={task}
              onChange={(e) => setTask(e.target.value)}
              placeholder="Enter text containing arXiv URLs to extract and download papers..."
              className="w-full h-28 px-3 py-2 bg-black/30 border border-white/20 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none text-sm"
              disabled={isRunning}
            />
          ) : mode === 'enhance-input' ? (
            /* Multi-line textarea for enhance-input mode */
            <textarea
              value={task}
              onChange={(e) => setTask(e.target.value)}
              placeholder="Enter task description containing arXiv URLs to enhance with contextual information..."
              className="w-full h-28 px-3 py-2 bg-black/30 border border-white/20 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none text-sm"
              disabled={isRunning}
            />
          ) : (
            /* Multi-line textarea for other modes */
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
          )}
        </div>

        {/* OpenAI Required Error - Main UI (Red Key) */}
        {(() => {
          const validation = getValidation();
          
          // Only show warning if credentials have been loaded and OpenAI is invalid
          const credentialsLoaded = credentialStatus !== null;
          const shouldShow = showOpenAIError || (credentialsLoaded && !validation.canSubmitTask && !validation.openaiValid);
          
          return shouldShow && (
            <div className="bg-red-900/50 border-2 border-red-500 rounded-lg p-4 text-sm animate-pulse">
              <div className="flex items-start gap-3">
                <div className="text-red-400 text-2xl">🚨</div>
                <div>
                  <div className="text-red-200 font-bold mb-2 text-base">⚠️ OpenAI API Key Required!</div>
                  <div className="text-red-200/90 text-sm leading-relaxed mb-3">
                    You must provide at least a valid OpenAI API key to submit tasks.
                    <br />
                    <br />📝 <strong>How to fix:</strong>
                    <br />• Click the <strong>red key icon (🔑)</strong> in the top right
                    <br />• Enter your OpenAI API key 
                    <br />• Click "Save & Test" to validate
                  </div>
                  <button
                    onClick={() => {
                      setShowOpenAIError(false)
                      setShowCredentialsModal(true)
                    }}
                    className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white text-sm font-medium rounded transition-colors"
                  >
                    🔑 Configure API Key
                  </button>
                </div>
              </div>
            </div>
          );
        })()}

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
              Advanced Configuration - {mode === 'one-shot' ? 'One Shot' : mode === 'planning-control' ? 'Deep Research' : mode === 'idea-generation' ? 'Idea Generation' : mode === 'arxiv' ? 'arXiv Filter' : mode === 'enhance-input' ? 'Enhance Input' : 'OCR'} Mode
            </h3>
            
            {/* Credential Status Message in Advanced Section */}
            {(() => {
              const validation = getValidation();
              if (!validation.canSubmitTask) {
                return (
                  <div className="text-xs text-red-400 bg-red-900/20 border border-red-500/20 rounded px-2 py-1">
                    ⚠️ {validation.statusMessage}
                  </div>
                );
              } else if (!validation.anthropicValid || !validation.vertexValid) {
                return (
                  <div className="text-xs text-orange-400 bg-orange-900/20 border border-orange-500/20 rounded px-2 py-1">
                    ℹ️ {validation.statusMessage}
                  </div>
                );
              }
              return null;
            })()}

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
              ) : mode === 'ocr' ? (
                /* OCR Configuration */
                <>
                  <div>
                    <Tooltip text="Save extracted text as Markdown files" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Save Markdown</label>
                    </Tooltip>
                    <select
                      value={config.saveMarkdown ? 'true' : 'false'}
                      onChange={(e) => setConfig({...config, saveMarkdown: e.target.value === 'true'})}
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    >
                      <option value="true">Yes</option>
                      <option value="false">No</option>
                    </select>
                  </div>

                  <div>
                    <Tooltip text="Save extracted text as JSON files with structured data" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Save JSON</label>
                    </Tooltip>
                    <select
                      value={config.saveJson ? 'true' : 'false'}
                      onChange={(e) => setConfig({...config, saveJson: e.target.value === 'true'})}
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    >
                      <option value="true">Yes</option>
                      <option value="false">No</option>
                    </select>
                  </div>

                  <div>
                    <Tooltip text="Save extracted text as plain text files" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Save Text</label>
                    </Tooltip>
                    <select
                      value={config.saveText ? 'true' : 'false'}
                      onChange={(e) => setConfig({...config, saveText: e.target.value === 'true'})}
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    >
                      <option value="true">Yes</option>
                      <option value="false">No</option>
                    </select>
                  </div>

                  <div>
                    <Tooltip text="Maximum number of parallel workers for processing multiple PDFs" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Max Workers</label>
                    </Tooltip>
                    <input
                      type="number"
                      value={config.maxWorkers || 4}
                      onChange={(e) => setConfig({...config, maxWorkers: parseInt(e.target.value)})}
                      min="1"
                      max="8"
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    />
                  </div>

                  <div className="col-span-2">
                    <Tooltip text="Directory where OCR output files will be saved (optional, defaults to input_path_processed)" wide position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Output Directory (optional)</label>
                    </Tooltip>
                    <input
                      type="text"
                      value={config.ocrOutputDir || ''}
                      onChange={(e) => setConfig({...config, ocrOutputDir: e.target.value})}
                      placeholder="Leave empty for auto-generated directory name"
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    />
                  </div>
                </>
              ) : mode === 'arxiv' ? (
                /* ArXiv Configuration - No specific options needed */
                <>
                  <div className="col-span-2">
                    <div className="text-xs text-gray-400 p-2 bg-blue-900/20 border border-blue-500/20 rounded">
                      ℹ️ arXiv Filter will scan your text for arXiv URLs and download the corresponding papers to the docs folder in your work directory.
                    </div>
                  </div>
                </>
              ) : mode === 'enhance-input' ? (
                /* Enhance Input Configuration */
                <>
                  <div className="col-span-2">
                    <div className="text-xs text-gray-400 p-2 bg-purple-900/20 border border-purple-500/20 rounded">
                      ✨ Enhance Input will scan your text for arXiv URLs, download papers, process them with OCR, create summaries, and append contextual information to your original text.
                    </div>
                  </div>
                  <div>
                    <Tooltip text="Maximum number of parallel workers for processing PDFs and summaries" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Max Workers</label>
                    </Tooltip>
                    <input
                      type="number"
                      value={config.maxWorkers || 2}
                      onChange={(e) => setConfig({...config, maxWorkers: parseInt(e.target.value)})}
                      min="1"
                      max="8"
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    />
                  </div>
                  <div>
                    <Tooltip text="AI model used for analyzing and summarizing downloaded papers" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Summarizer Model</label>
                    </Tooltip>
                    <select
                      value={config.summarizerModel || 'gpt-4.1-2025-04-14'}
                      onChange={(e) => setConfig({...config, summarizerModel: e.target.value})}
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    >
                      <option value="gpt-4.1-2025-04-14">GPT-4.1</option>
                      <option value="gpt-4o">GPT-4o</option>
                      <option value="gpt-5-2025-08-07">GPT-5</option>
                      <option value="claude-sonnet-4-20250514">Claude Sonnet 4</option>
                      <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
                      <option value="gpt-4o-mini">GPT-4o Mini</option>
                      <option value="o3-mini-2025-01-31">o3-mini</option>
                    </select>
                  </div>
                  <div>
                    <Tooltip text="Model used for formatting and structuring summary output" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Default Formatter Model</label>
                    </Tooltip>
                    <select
                      value={config.defaultFormatterModel || 'o3-mini-2025-01-31'}
                      onChange={(e) => setConfig({...config, defaultFormatterModel: e.target.value})}
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    >
                      <option value="o3-mini-2025-01-31">o3-mini</option>
                      <option value="gpt-4.1-2025-04-14">GPT-4.1</option>
                      <option value="gpt-4o-mini">GPT-4o Mini</option>
                      <option value="gpt-4o">GPT-4o</option>
                      <option value="gpt-5-2025-08-07">GPT-5</option>
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

              {/* Global Model Options - Available for all modes except OCR, arXiv, and enhance-input */}
              {mode !== 'ocr' && mode !== 'arxiv' && mode !== 'enhance-input' && (
                <>
                  <div>
                    <Tooltip text="Default model used for general orchestration tasks and fallback scenarios" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Default Model</label>
                    </Tooltip>
                    <select
                      value={config.defaultModel || 'gpt-4.1-2025-04-14'}
                      onChange={(e) => setConfig({...config, defaultModel: e.target.value})}
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    >
                      <option value="gpt-4.1-2025-04-14">GPT-4.1</option>
                      <option value="o3-mini-2025-01-31">o3-mini</option>
                      <option value="gpt-4o-mini">GPT-4o Mini</option>
                      <option value="gpt-4o">GPT-4o</option>
                      <option value="gpt-5-2025-08-07">GPT-5</option>
                    </select>
                  </div>

                  <div>
                    <Tooltip text="Model used for formatting and structuring output" position="bottom">
                      <label className="block text-xs text-gray-400 mb-1">Default Formatter Model</label>
                    </Tooltip>
                    <select
                      value={config.defaultFormatterModel || 'o3-mini-2025-01-31'}
                      onChange={(e) => setConfig({...config, defaultFormatterModel: e.target.value})}
                      className="w-full px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
                      disabled={isRunning}
                    >
                      <option value="o3-mini-2025-01-31">o3-mini</option>
                      <option value="gpt-4.1-2025-04-14">GPT-4.1</option>
                      <option value="gpt-4o-mini">GPT-4o Mini</option>
                      <option value="gpt-4o">GPT-4o</option>
                      <option value="gpt-5-2025-08-07">GPT-5</option>
                    </select>
                  </div>

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
                </>
              )}

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
                      value={config.nPlanReviews}
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

              {/* Working Directory */}
              <div className="col-span-2">
                <Tooltip text="Directory where task files, results, and outputs will be saved" wide position="bottom">
                  <label className="block text-xs text-gray-400 mb-1">Working Directory</label>
                </Tooltip>
                <div className="space-y-2">
                  <div className="flex gap-1">
                    <input
                      type="text"
                      value={config.workDir}
                      onChange={(e) => setConfig({...config, workDir: e.target.value})}
                      placeholder="~/cmbagent_workdir"
                      className="flex-1 px-2 py-1 bg-black/30 border border-white/20 rounded text-white text-xs focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-transparent"
                      disabled={isRunning}
                    />
                    <button
                      type="button"
                      onClick={() => setConfig({...config, workDir: '~/cmbagent_workdir'})}
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
            </div>
          </div>
        )}



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
      
      {/* Credentials Modal */}
      <CredentialsModal
        isOpen={showCredentialsModal}
        onClose={() => setShowCredentialsModal(false)}
        onCredentialsUpdated={() => {
          refreshCredentials(); // Use the hook's refresh function
          setShowOpenAIError(false); // Clear OpenAI error message
        }}
      />
    </div>
  )
}
