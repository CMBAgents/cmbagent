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

export default function IdeaGenerationPage() {
  const router = useRouter()
  const [currentMode, setCurrentMode] = useState('idea_generation')
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
    max_plan_steps: 6,
    plan_instructions: `Given these datasets, and information, make a plan according to the following instructions: 

- Ask idea_maker to generate 5 new research project ideas related to the datasets.
- Ask idea_hater to critique these ideas.
- Ask idea_maker to select and improve 2 out of the 5 research project ideas given the output of the idea_hater.
- Ask idea_hater to critique the 2 improved ideas. 
- Ask idea_maker to select the best idea out of the 2. 
- Ask idea_maker to report the best idea in the form of a scientific paper title with a 5-sentence description. 

The goal of this task is to generate a research project idea based on the data of interest. 
Don't suggest to perform any calculations or analyses here. The only goal of this task is to obtain the best possible project idea.`
  })

  const handleModeChange = (mode: string) => {
    setCurrentMode(mode)
    if (mode !== 'idea_generation') {
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
      max_plan_steps: 6,
      plan_instructions: `Given these datasets, and information, make a plan according to the following instructions: 

- Ask idea_maker to generate 5 new research project ideas related to the datasets.
- Ask idea_hater to critique these ideas.
- Ask idea_maker to select and improve 2 out of the 5 research project ideas given the output of the idea_hater.
- Ask idea_hater to critique the 2 improved ideas. 
- Ask idea_maker to select the best idea out of the 2. 
- Ask idea_maker to report the best idea in the form of a scientific paper title with a 5-sentence description. 

The goal of this task is to generate a research project idea based on the data of interest. 
Don't suggest to perform any calculations or analyses here. The only goal of this task is to obtain the best possible project idea.`
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
      await new Promise(resolve => setTimeout(resolve, 4000))

      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: `I've processed your idea generation request: "${message}". This mode will use the idea_maker and idea_hater agents to generate, critique, and refine research project ideas through ${parameters.max_plan_steps} steps.`,
        timestamp: new Date(),
        reasoning: 'Idea generation mode uses specialized agents to brainstorm, critique, and refine research project ideas through an iterative process.',
        elapsed: '4.0s'
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
