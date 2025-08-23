'use client'

import { useState } from 'react'
import { FileText, Eye, EyeOff, Folder } from 'lucide-react'
import FileBrowser from './FileBrowser'

interface ResultDisplayProps {
  results?: {
    chat_history?: any[]
    final_context?: any
    execution_time?: number
    files?: string[]
    plots?: string[]
    code_outputs?: string[]
    work_dir?: string
    base_work_dir?: string
  } | null
}

export default function ResultDisplay({ results }: ResultDisplayProps) {
  const [activeTab, setActiveTab] = useState('summary')
  const [showDetails, setShowDetails] = useState(false)

  // Show placeholder when no results
  if (!results) {
    return (
      <div className="h-full flex flex-col bg-black/20 rounded-xl border border-white/20">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-white/10">
          <h2 className="text-lg font-semibold text-white">Results</h2>
          <div className="text-sm text-gray-400">Ready</div>
        </div>

        {/* Placeholder Content */}
        <div className="flex-1 flex items-center justify-center p-8">
          <div className="text-center">
            <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-blue-500/20 flex items-center justify-center">
              <FileText className="w-8 h-8 text-blue-400" />
            </div>
            <h3 className="text-lg font-medium text-white mb-2">No Results Yet</h3>
            <p className="text-gray-400 text-sm">
              Submit a task to see results, files, and execution details here.
            </p>
          </div>
        </div>
      </div>
    )
  }

  const tabs = [
    { id: 'summary', label: 'Summary', icon: FileText },
    { id: 'files', label: 'Files', icon: Folder },
  ]

  const renderSummary = () => (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-black/20 rounded-lg p-4">
          <h4 className="text-sm font-medium text-gray-300 mb-2">Execution Time</h4>
          <p className="text-2xl font-bold text-green-400">
            {results.execution_time ? `${results.execution_time.toFixed(2)}s` : 'N/A'}
          </p>
        </div>
        
        <div className="bg-black/20 rounded-lg p-4">
          <h4 className="text-sm font-medium text-gray-300 mb-2">Status</h4>
          <p className="text-2xl font-bold text-green-400">✅ Complete</p>
        </div>
      </div>

      {results.final_context && (
        <div className="bg-black/20 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-sm font-medium text-gray-300">Final Result</h4>
            <button
              onClick={() => setShowDetails(!showDetails)}
              className="flex items-center space-x-1 text-xs text-blue-400 hover:text-blue-300"
            >
              {showDetails ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
              <span>{showDetails ? 'Hide' : 'Show'} Details</span>
            </button>
          </div>

          {showDetails ? (
            <pre className="text-xs text-gray-300 bg-black/30 p-3 rounded overflow-x-auto">
              {JSON.stringify(results.final_context, null, 2)}
            </pre>
          ) : (
            <p className="text-gray-300">
              Task completed successfully. Click "Show Details" to view the full context.
            </p>
          )}
        </div>
      )}

      {results.work_dir && (
        <div className="bg-black/20 rounded-lg p-4">
          <h4 className="text-sm font-medium text-gray-300 mb-2">Working Directory</h4>
          <p className="text-xs text-gray-400 font-mono">{results.work_dir}</p>
          <p className="text-xs text-gray-500 mt-1">
            Generated files are saved in: chats, codebase, cost, data, time folders
          </p>
        </div>
      )}
    </div>
  )





  const renderFiles = () => {
    if (!results.base_work_dir) {
      return (
        <div className="text-center text-gray-500 py-8">
          <Folder className="w-12 h-12 mx-auto mb-4 opacity-50" />
          <p>No working directory available</p>
        </div>
      )
    }

    return (
      <div className="h-full">
        <FileBrowser
          workDir={results.base_work_dir}
          onFileSelect={() => {
            // File selected in browser
          }}
        />
      </div>
    )
  }

  return (
    <div className="bg-white/10 backdrop-blur-sm rounded-xl border border-white/20 overflow-hidden">
      {/* Header */}
      <div className="p-4 bg-black/40 border-b border-white/10">
        <h3 className="text-white font-medium">Execution Results</h3>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-white/10">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center space-x-2 px-4 py-3 text-sm font-medium transition-colors ${
              activeTab === tab.id
                ? 'text-blue-400 border-b-2 border-blue-400 bg-blue-400/10'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            <tab.icon className="w-4 h-4" />
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="p-3 flex-1 overflow-y-auto console-scrollbar min-h-0">
        {activeTab === 'summary' && renderSummary()}
        {activeTab === 'files' && renderFiles()}
      </div>
    </div>
  )
}
