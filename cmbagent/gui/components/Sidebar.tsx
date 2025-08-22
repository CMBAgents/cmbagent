'use client'

import { useState } from 'react'
import { Settings, Bot, Key, RotateCcw } from 'lucide-react'

interface SidebarProps {
  onModeChange: (mode: string) => void
  onAgentModelChange: (agent: string, model: string) => void
  onApiKeyChange: (provider: string, key: string) => void
  onResetSession: () => void
  currentMode: string
  agentModels: Record<string, string>
}

export default function Sidebar({
  onModeChange,
  onAgentModelChange,
  onApiKeyChange,
  onResetSession,
  currentMode,
  agentModels
}: SidebarProps) {
  const [showApiKeys, setShowApiKeys] = useState(false)

  const modes = [
    { id: 'one_shot', label: 'One Shot' },
    { id: 'planning_and_control', label: 'Planning & Control' },
    { id: 'idea_generation', label: 'Idea Generation' },
    { id: 'human_in_the_loop', label: 'Human in the Loop' }
  ]

  // Exact model lists from Streamlit gui.py
  const agents = {
    engineer: {
      label: 'Engineer',
      models: [
        'gpt-4.1-2025-04-14', 'gpt-5-2025-08-07', 'gemini-2.5-pro', 'claude-sonnet-4-20250514', 
        'gpt-4o', 'gpt-4o-mini', 'gpt-4.1-mini', 'gpt-4.5-preview', 'o3', 'o4-mini', 'o3-mini',
        'gemini-2.0-flash'
      ]
    },
    researcher: {
      label: 'Researcher',
      models: [
        'gpt-4.1-2025-04-14', 'gpt-5-2025-08-07', 'claude-sonnet-4-20250514', 'gpt-4o', 
        'gpt-4o-mini', 'gpt-4.1-mini', 'gpt-4.5-preview', 'o3', 'o4-mini', 'o3-mini',
        'gemini-2.5-pro', 'gemini-2.0-flash'
      ]
    }
  }

  return (
    <div className="w-80 bg-white shadow-sm border-r border-gray-200 h-screen overflow-y-auto">
      <div className="p-6">
        {/* Mode Selection */}
        <div className="mb-8">
          <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center">
            <Settings size={20} className="mr-2" />
            Mode
          </h3>
          <div className="space-y-2">
            {modes.map((mode) => (
              <button
                key={mode.id}
                onClick={() => onModeChange(mode.id)}
                className={`w-full text-left p-3 rounded-lg border transition-colors ${
                  currentMode === mode.id
                    ? 'border-blue-500 bg-blue-50 text-blue-700'
                    : 'border-gray-200 hover:border-gray-300'
                }`}
              >
                {mode.label}
              </button>
            ))}
          </div>
        </div>

        {/* Agents Configuration */}
        <div className="mb-8">
          <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center">
            <Bot size={20} className="mr-2" />
            Agents
          </h3>
          <div className="space-y-4">
            {Object.entries(agents).map(([key, info]) => (
              <div key={key}>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  {info.label}
                </label>
                <select
                  value={agentModels[key] || info.models[0]}
                  onChange={(e) => onAgentModelChange(key, e.target.value)}
                  className="w-full p-2 border border-gray-300 rounded-md text-sm"
                >
                  {info.models.map((model) => (
                    <option key={model} value={model}>
                      {model}
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        </div>

        {/* API Keys */}
        <div className="mb-8">
          <button
            onClick={() => setShowApiKeys(!showApiKeys)}
            className="w-full flex items-center justify-between p-3 text-left border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <span className="flex items-center">
              <Key size={20} className="mr-2" />
              API Keys
            </span>
            <span className="text-gray-500">{showApiKeys ? '−' : '+'}</span>
          </button>
          
          {showApiKeys && (
            <div className="mt-3 space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  OpenAI
                </label>
                <input
                  type="password"
                  placeholder="sk-..."
                  onChange={(e) => onApiKeyChange('OPENAI', e.target.value)}
                  className="w-full p-2 border border-gray-300 rounded-md text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Anthropic
                </label>
                <input
                  type="password"
                  placeholder="sk-ant-..."
                  onChange={(e) => onApiKeyChange('ANTHROPIC', e.target.value)}
                  className="w-full p-2 border border-gray-300 rounded-md text-sm"
                />
              </div>
            </div>
          )}
        </div>

        {/* Reset Session */}
        <div className="border-t pt-6">
          <button
            onClick={onResetSession}
            className="w-full flex items-center justify-center space-x-2 bg-red-500 hover:bg-red-600 text-white font-medium py-2 px-4 rounded-lg transition-colors"
          >
            <RotateCcw size={16} />
            <span>Reset Session</span>
          </button>
        </div>
      </div>
    </div>
  )
}
