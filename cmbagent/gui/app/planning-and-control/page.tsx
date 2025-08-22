'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Sidebar from '../../components/Sidebar'
import ParametersPanel from '../../components/ParametersPanel'
import ChatInterface from '../../components/ChatInterface'
import { ArrowLeft } from 'lucide-react'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string | React.ReactNode
  timestamp: Date
  reasoning?: string
  elapsed?: string
}

export default function PlanningAndControlPage() {
  const router = useRouter()
  const [currentMode, setCurrentMode] = useState('planning_and_control')
  const [agentModels, setAgentModels] = useState({
    engineer: 'gpt-4o',
    researcher: 'gpt-4o'
  })
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({})
  const [messages, setMessages] = useState<Message[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [parameters, setParameters] = useState({
    max_rounds_control: 500,
    n_plan_reviews: 1,
    max_n_attempts: 6,
    max_plan_steps: 4,
    plan_instructions: "Use engineer agent for the whole analysis, and researcher at the very end in the last step to comment on results.",
    hardware_constraints: "We are running this session on a laptop, and no calculations should require more than 10 minutes, 500MB of RAM, 500MB of disk space and 4 CPUs."
  })

  const handleModeChange = (mode: string) => {
    setCurrentMode(mode)
    if (mode !== 'planning_and_control') {
      router.push(`/${mode.replace('_', '-')}`)
    }
  }

  const handleAgentModelChange = (agent: string, model: string) => {
    setAgentModels(prev => ({
      ...prev,
      [agent]: model
    }))
  }

  const handleApiKeyChange = (provider: string, key: string) => {
    setApiKeys(prev => ({
      ...prev,
      [provider]: key
    }))
  }

  const handleResetSession = () => {
    setMessages([])
    setParameters({
      max_rounds_control: 500,
      n_plan_reviews: 1,
      max_n_attempts: 6,
      max_plan_steps: 4,
      plan_instructions: "Use engineer agent for the whole analysis, and researcher at the very end in the last step to comment on results.",
      hardware_constraints: "We are running this session on a laptop, and no calculations should require more than 10 minutes, 500MB of RAM, 500MB of disk space and 4 CPUs."
    })
  }

  const handleParameterChange = (key: string, value: any) => {
    setParameters(prev => ({
      ...prev,
      [key]: value
    }))
  }

  const handleSendMessage = async (message: string) => {
    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: message,
      timestamp: new Date()
    }

    setMessages(prev => [...prev, userMessage])
    setIsLoading(true)

    try {
      // Simulate API call to CMBAgent backend
      await new Promise(resolve => setTimeout(resolve, 3000))

      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: `I've processed your planning and control request: "${message}". This mode will create a multi-step plan with ${parameters.max_plan_steps} steps and ${parameters.n_plan_reviews} review rounds.`,
        timestamp: new Date(),
        reasoning: 'Planning and control mode analyzes the task, creates a structured plan, and executes it step by step with iterative reviews.',
        elapsed: '3.0s'
      }

      setMessages(prev => [...prev, assistantMessage])
    } catch (error) {
      console.error('Error processing message:', error)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="flex h-screen bg-gray-50">
      <Sidebar
        onModeChange={handleModeChange}
        onAgentModelChange={handleAgentModelChange}
        onApiKeyChange={handleApiKeyChange}
        onResetSession={handleResetSession}
        currentMode={currentMode}
        agentModels={agentModels}
      />
      
      <div className="flex-1 flex flex-col">
        {/* Back Button */}
        <div className="bg-white border-b border-gray-200 p-4">
          <button
            onClick={() => router.push('/')}
            className="flex items-center space-x-2 text-cmb-blue hover:text-blue-800 transition-colors"
          >
            <ArrowLeft size={20} />
            <span>Back to Mode Selection</span>
          </button>
        </div>

        {/* Parameters Panel */}
        <ParametersPanel
          currentMode={currentMode}
          parameters={parameters}
          onParameterChange={handleParameterChange}
        />

        {/* Chat Interface */}
        <div className="flex-1">
          <ChatInterface
            messages={messages}
            onSendMessage={handleSendMessage}
            isLoading={isLoading}
            currentMode={currentMode}
          />
        </div>
      </div>
    </div>
  )
}
