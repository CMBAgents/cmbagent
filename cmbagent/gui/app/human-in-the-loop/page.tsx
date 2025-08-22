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

export default function HumanInTheLoopPage() {
  const router = useRouter()
  const [currentMode, setCurrentMode] = useState('human_in_the_loop')
  const [agentModels, setAgentModels] = useState({
    engineer: 'gpt-4o',
    researcher: 'gpt-4o'
  })
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({})
  const [selectedAgent, setSelectedAgent] = useState('engineer')
  const [messages, setMessages] = useState<Message[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [parameters, setParameters] = useState({
    max_rounds: 50,
    max_n_attempts: 6
  })

  const handleModeChange = (mode: string) => {
    setCurrentMode(mode)
    if (mode !== 'human_in_the_loop') {
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
      max_rounds: 50,
      max_n_attempts: 6
    })
    setSelectedAgent('engineer')
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
      await new Promise(resolve => setTimeout(resolve, 2500))

      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: `I've processed your human-in-the-loop request: "${message}". This mode maintains context across turns, allowing for follow-up questions and iterative refinement.`,
        timestamp: new Date(),
        reasoning: 'Human-in-the-loop mode maintains conversation memory and context, enabling iterative problem-solving and follow-up questions.',
        elapsed: '2.5s'
      }

      setMessages(prev => [...prev, assistantMessage])
    } catch (error) {
      console.error('Error processing message:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const handleAgentSelect = (agent: string) => {
    setSelectedAgent(agent)
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
            onAgentSelect={handleAgentSelect}
            selectedAgent={selectedAgent}
          />
        </div>
      </div>
    </div>
  )
}
