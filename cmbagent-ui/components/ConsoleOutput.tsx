'use client'

import { useEffect, useRef, useState } from 'react'
import { Terminal, Copy, Trash2, ArrowDown } from 'lucide-react'

interface ConsoleOutputProps {
  output: string[]
  isRunning: boolean
  onClear?: () => void
}

export default function ConsoleOutput({ output, isRunning, onClear }: ConsoleOutputProps) {
  const consoleRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const [showScrollButton, setShowScrollButton] = useState(false)

  // Auto-scroll to bottom when new output is added
  useEffect(() => {
    const scrollToBottom = () => {
      if (consoleRef.current) {
        // Always scroll to bottom when new output arrives
        consoleRef.current.scrollTop = consoleRef.current.scrollHeight
      }
    }

    // Use setTimeout to ensure DOM is updated
    const timeoutId = setTimeout(scrollToBottom, 50)
    return () => clearTimeout(timeoutId)
  }, [output])

  // Also scroll when running state changes
  useEffect(() => {
    if (isRunning && consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight
    }
  }, [isRunning])

  // Handle scroll detection for showing scroll-to-bottom button
  useEffect(() => {
    const handleScroll = () => {
      if (consoleRef.current) {
        const { scrollTop, scrollHeight, clientHeight } = consoleRef.current
        const isNearBottom = scrollHeight - scrollTop - clientHeight < 50
        setShowScrollButton(!isNearBottom && output.length > 3)
      }
    }

    const consoleElement = consoleRef.current
    if (consoleElement) {
      consoleElement.addEventListener('scroll', handleScroll)
      return () => consoleElement.removeEventListener('scroll', handleScroll)
    }
  }, [output.length])

  const scrollToBottom = () => {
    if (consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight
    }
  }

  const copyToClipboard = () => {
    const text = output.join('\n')
    navigator.clipboard.writeText(text)
  }

  const clearConsole = () => {
    if (onClear) {
      onClear()
    }
  }

  const formatOutput = (line: string, index: number) => {
    // Detect different types of output and apply appropriate styling
    let className = 'text-console-text'
    let prefix = ''

    if (line.includes('ERROR') || line.includes('Error') || line.includes('error')) {
      className = 'text-console-error'
      prefix = '❌ '
    } else if (line.includes('WARNING') || line.includes('Warning') || line.includes('warning')) {
      className = 'text-console-warning'
      prefix = '⚠️ '
    } else if (line.includes('SUCCESS') || line.includes('Success') || line.includes('✓')) {
      className = 'text-console-success'
      prefix = '✅ '
    } else if (line.includes('INFO') || line.includes('Info')) {
      className = 'text-console-info'
      prefix = 'ℹ️ '
    } else if (line.startsWith('>>>') || line.startsWith('$')) {
      className = 'text-blue-400'
      prefix = '🔧 '
    } else if (line.includes('Code Explanation:')) {
      className = 'text-yellow-400 font-semibold'
      prefix = '📝 '
    } else if (line.includes('Python Code:')) {
      className = 'text-green-400 font-semibold'
      prefix = '🐍 '
    } else if (line.includes('FINAL RESULT:')) {
      className = 'text-purple-400 font-bold'
      prefix = '🎯 '
    }

    return (
      <div key={index} className={`${className} font-mono text-xs leading-tight`}>
        <span className="text-gray-500 select-none mr-2">
          {String(index + 1).padStart(3, '0')}
        </span>
        <span className="select-none mr-1">{prefix}</span>
        <span className="whitespace-pre-wrap">{line}</span>
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col bg-console-bg rounded-xl border border-white/20 overflow-hidden relative">
      {/* Console Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-black/40 border-b border-white/10">
        <div className="flex items-center space-x-2">
          <Terminal className="w-5 h-5 text-green-400" />
          {/* <h3 className="text-white font-medium text-sm">Console Output</h3> */}
          {isRunning && (
            <div className="flex items-center space-x-1">
              <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse"></div>
              <span className="text-green-400 text-xs">Running</span>
            </div>
          )}
        </div>
        
        <div className="flex items-center space-x-2">
          <button
            onClick={scrollToBottom}
            className="p-1.5 text-gray-400 hover:text-white transition-colors"
            title="Scroll to bottom"
          >
            <ArrowDown className="w-4 h-4" />
          </button>
          <button
            onClick={copyToClipboard}
            className="p-1.5 text-gray-400 hover:text-white transition-colors"
            title="Copy to clipboard"
          >
            <Copy className="w-4 h-4" />
          </button>
          <button
            onClick={clearConsole}
            className="p-1.5 text-gray-400 hover:text-white transition-colors"
            title="Clear console"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Console Content */}
      <div
        ref={consoleRef}
        className="flex-1 px-3 py-2 overflow-y-auto console-scrollbar"
        style={{ minHeight: 0 }}
      >
        {output.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-500">
            <div className="text-center">
              <Terminal className="w-12 h-12 mx-auto mb-4 opacity-50" />
              <p>Console output will appear here...</p>
              <p className="text-sm mt-2">Submit a task to get started</p>
            </div>
          </div>
        ) : (
          <div className="space-y-1">
            {output.map((line, index) => formatOutput(line, index))}
            {isRunning && (
              <div className="flex items-center space-x-2 text-green-400 font-mono text-xs">
                <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse"></div>
                <span className="typing-animation">Processing...</span>
              </div>
            )}
            <div ref={endRef} />
          </div>
        )}
      </div>

      {/* Floating Scroll to Bottom Button */}
      {showScrollButton && (
        <button
          onClick={scrollToBottom}
          className="absolute bottom-20 right-6 p-3 bg-blue-600 hover:bg-blue-700 text-white rounded-full shadow-lg transition-all duration-200 z-10"
          title="Scroll to bottom"
        >
          <ArrowDown className="w-5 h-5" />
        </button>
      )}

      {/* Console Footer */}
      <div className="px-3 py-1.5 bg-black/40 border-t border-white/10">
        <div className="flex items-center justify-between text-xs text-gray-400">
          <span>{output.length} lines</span>
          <span>CMBAgent Console v1.0</span>
        </div>
      </div>
    </div>
  )
}
