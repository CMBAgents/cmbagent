'use client'

import { useState, useRef, useEffect } from 'react'
import { Send, Bot, User } from 'lucide-react'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string | React.ReactNode
  timestamp: Date
  reasoning?: string
  elapsed?: string
}

interface ChatInterfaceProps {
  messages: Message[]
  onSendMessage: (message: string) => void
  isLoading: boolean
  currentMode: string
  onAgentSelect?: (agent: string) => void
  selectedAgent?: string
}

export default function ChatInterface({
  messages,
  onSendMessage,
  isLoading,
  currentMode,
  onAgentSelect,
  selectedAgent
}: ChatInterfaceProps) {
  const [inputValue, setInputValue] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (inputValue.trim() && !isLoading) {
      onSendMessage(inputValue.trim())
      setInputValue('')
    }
  }

  const examplePrompts = {
    one_shot: [
      "Plot a 3D Möbius strip using matplotlib.",
      "Compute and plot cmb temperature power spectrum using camb.",
      "What is the difference between a black hole and a white hole?"
    ],
    planning_and_control: [
      "Generate simulated stock market data during Trump's tariffs to test validity of the Black-Scholes pricing model under such conditions.",
      "Write a pipeline to illustrate how to constrain H0 using gravitational wave data."
    ],
    idea_generation: [
      "Bank customer data during covid-19."
    ],
    human_in_the_loop: [
      "Generate a Poisson point process on S^2, compute the corresponding scalar field using a Gaussian smoothing kernel, and plot both the field and its angular power spectrum."
    ]
  }

  const getModeTitle = (mode: string) => {
    switch (mode) {
      case 'one_shot': return 'One Shot'
      case 'planning_and_control': return 'Planning & Control'
      case 'idea_generation': return 'Idea Generation'
      case 'human_in_the_loop': return 'Human in the Loop'
      default: return mode
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Simple Header */}
      <div className="bg-white border-b border-gray-200 p-4">
        <h1 className="text-2xl font-semibold text-gray-900 text-center">
          {getModeTitle(currentMode)}
        </h1>
      </div>

      {/* Agent Selection */}
      {(currentMode === 'one_shot' || currentMode === 'human_in_the_loop') && onAgentSelect && (
        <div className="bg-gray-50 border-b border-gray-200 p-4">
          <div className="max-w-2xl mx-auto">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Select Agent
            </label>
            <div className="flex space-x-3">
              {[
                { id: 'engineer', label: 'Engineer' },
                { id: 'researcher', label: 'Researcher' },
                ...(currentMode === 'one_shot' ? [
                  { id: 'camb_context', label: 'Camb' },
                  { id: 'classy_context', label: 'Classy' }
                ] : [])
              ].map((agent) => (
                <button
                  key={agent.id}
                  onClick={() => onAgentSelect(agent.id)}
                  className={`px-4 py-2 rounded-lg border text-sm transition-colors ${
                    selectedAgent === agent.id
                      ? 'border-blue-500 bg-blue-50 text-blue-700'
                      : 'border-gray-200 hover:border-gray-300'
                  }`}
                >
                  {agent.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="max-w-2xl mx-auto text-center">
            <p className="text-gray-600 mb-4">Try one of these examples or type your own task:</p>
            <div className="space-y-2">
              {examplePrompts[currentMode as keyof typeof examplePrompts]?.map((prompt, index) => (
                <button
                  key={index}
                  onClick={() => onSendMessage(prompt)}
                  className="w-full p-3 text-left bg-gray-50 hover:bg-gray-100 rounded-lg border border-gray-200 transition-colors text-sm"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((message) => (
          <div
            key={message.id}
            className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-2xl rounded-lg p-4 ${
                message.role === 'user'
                  ? 'bg-blue-500 text-white'
                  : 'bg-white border border-gray-200'
              }`}
            >
              <div className="flex items-start space-x-3">
                <div className="flex-shrink-0">
                  {message.role === 'user' ? (
                    <User size={16} className="text-white" />
                  ) : (
                    <Bot size={16} className="text-blue-500" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm">
                    {typeof message.content === 'string' ? message.content : 'Content'}
                  </div>
                  {message.elapsed && (
                    <div className="text-xs text-gray-400 mt-2">
                      Completed in {message.elapsed}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        ))}

        {isLoading && (
          <div className="flex justify-start">
            <div className="max-w-2xl rounded-lg p-4 bg-white border border-gray-200">
              <div className="flex items-center space-x-3">
                <Bot size={16} className="text-blue-500" />
                <div className="flex items-center space-x-2">
                  <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-500"></div>
                  <span className="text-gray-600 text-sm">Processing...</span>
                </div>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="bg-white border-t border-gray-200 p-4">
        <form onSubmit={handleSubmit} className="max-w-4xl mx-auto">
          <div className="flex space-x-3">
            <input
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder="Type your task or question here..."
              className="flex-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              disabled={isLoading}
            />
            <button
              type="submit"
              disabled={!inputValue.trim() || isLoading}
              className="px-6 py-3 bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <Send size={16} />
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
