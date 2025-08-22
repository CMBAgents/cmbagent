'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Sidebar from '../../components/Sidebar'
import ParametersPanel from '../../components/ParametersPanel'
import ChatInterface from '../../components/ChatInterface'
import { ArrowLeft } from 'lucide-react'
import { cmbAgentAPI, CMBAgentRequest, CMBAgentResponse } from '../../services/cmbagent-api'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string | React.ReactNode
  timestamp: Date
  reasoning?: string
  elapsed?: string
}

export default function OneShotPage() {
  const router = useRouter()
  const [currentMode, setCurrentMode] = useState('one_shot')
  const [agentModels, setAgentModels] = useState({
    engineer: 'gpt-4.1-2025-04-14',
    researcher: 'gpt-4.1-2025-04-14'
  })
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({})
  const [selectedAgent, setSelectedAgent] = useState('engineer')
  const [messages, setMessages] = useState<Message[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [parameters, setParameters] = useState({
    max_rounds: 25,
    max_n_attempts: 6
  })

  const handleModeChange = (mode: string) => {
    setCurrentMode(mode)
    if (mode !== 'one_shot') {
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
      max_rounds: 25,
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

    const startTime = Date.now()

    try {
      // Prepare the request similar to Streamlit GUI
      const request: CMBAgentRequest = {
        user_input: message,
        max_rounds: parameters.max_rounds,
        max_n_attempts: parameters.max_n_attempts,
        engineer_model: agentModels.engineer,
        researcher_model: agentModels.researcher,
        agent: selectedAgent,
        api_keys: apiKeys,
        work_dir: 'project_dir' // This would be configurable
      }

      // Call the CMBAgent API (now using the real endpoint)
      const results: CMBAgentResponse = await cmbAgentAPI.oneShot(request)
      
      // Process the response similar to Streamlit GUI
      const history = results.chat_history || []
      const toolResponses = results.tool_responses || []

      // Build displayable content from chat history
      const toDisplay: Array<[string, any]> = []
      const seen = new Set()

      for (const turn of history) {
        const name = turn.name || ''
        const content = turn.content

        if (!name || content === null || content === undefined) {
          continue
        }

        // Simple dedupe key
        const key = `${name}::${String(content).substring(0, 200)}`
        if (seen.has(key)) {
          continue
        }
        seen.add(key)

        // Extract displayable content
        let displayContent = content
        if (typeof content === 'object' && content.data !== undefined) {
          displayContent = content.data
        }

        toDisplay.push([name, displayContent])
      }

      // Create the assistant message with the processed content
      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1)
      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: toDisplay.length > 0 ? toDisplay[0][1] : 'No response content',
        timestamp: new Date(),
        reasoning: 'Response from CMBAgent one_shot function',
        elapsed: `${elapsed}s`
      }

      setMessages(prev => [...prev, assistantMessage])

    } catch (error) {
      console.error('Error processing message:', error)
      
      // Add error message
      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1)
      const errorMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: `❌ An error occurred while processing your request: ${error instanceof Error ? error.message : 'Unknown error'}`,
        timestamp: new Date(),
        reasoning: 'Error handling',
        elapsed: `${elapsed}s`
      }

      setMessages(prev => [...prev, errorMessage])
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
            className="flex items-center space-x-2 text-blue-600 hover:text-blue-800 transition-colors"
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
