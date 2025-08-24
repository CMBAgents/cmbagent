'use client'

import { useState, useEffect } from 'react'
import { FileText, Folder, DollarSign } from 'lucide-react'
import FileBrowser from './FileBrowser'

interface CostData {
  Agent: string
  'Cost ($)': number
  'Prompt Tokens': number
  'Completion Tokens': number
  'Total Tokens': number
  Model: string
}

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
  const [costData, setCostData] = useState<CostData[]>([])
  const [isLoadingCost, setIsLoadingCost] = useState(false)
  const [costLoadError, setCostLoadError] = useState<string | null>(null)
  const [costLoadAttempts, setCostLoadAttempts] = useState(0)
  const [lastCostUpdate, setLastCostUpdate] = useState<Date | null>(null)

  // Improved function to load cost data with retry logic and error handling
  const loadCostData = async (retryAttempt = 0): Promise<void> => {
    if (!results?.work_dir) {
      console.log('No work directory available')
      setCostLoadError('No work directory available')
      return
    }

    const maxRetries = 5
    const retryDelays = [2000, 3000, 5000, 8000, 12000] // Exponential backoff

    setIsLoadingCost(true)
    setCostLoadError(null)
    setCostLoadAttempts(retryAttempt + 1)
    
    console.log(`Loading cost data from: ${results.work_dir} (attempt ${retryAttempt + 1}/${maxRetries})`)

    try {
      // Direct path to cost folder
      const costDir = `${results.work_dir}/cost`
      console.log('Cost directory:', costDir)

      // List files in cost directory
      const listResponse = await fetch(`/api/files/list?path=${encodeURIComponent(costDir)}`)
      
      if (!listResponse.ok) {
        if (listResponse.status === 404) {
          throw new Error('Cost directory not found - task may still be generating cost reports')
        }
        throw new Error(`Failed to list cost directory: ${listResponse.status}`)
      }

      const listData = await listResponse.json()
      console.log('Cost directory listing:', listData)

      // Find the most recent cost report file
      const costFiles = listData.items?.filter((file: any) => 
        file.name.startsWith('cost_report_') && file.name.endsWith('.json')
      ) || []

      if (costFiles.length === 0) {
        throw new Error('No cost report files found - task may still be generating reports')
      }

      // Sort by modification time to get the most recent
      costFiles.sort((a: any, b: any) => (b.modified || 0) - (a.modified || 0))
      const latestCostFile = costFiles[0]
      console.log('Latest cost file:', latestCostFile)

      // Read the cost data
      const contentResponse = await fetch(`/api/files/content?path=${encodeURIComponent(latestCostFile.path)}`)
      
      if (!contentResponse.ok) {
        throw new Error(`Failed to read cost file: ${contentResponse.status}`)
      }

      const contentData = await contentResponse.json()
      console.log('Cost file content loaded')

      if (contentData.content) {
        // Clean the JSON content to handle NaN values
        let cleanedContent = contentData.content
        
        // Replace NaN with null in the JSON string
        cleanedContent = cleanedContent.replace(/:\s*NaN/g, ': null')
        
        try {
          const parsedCostData = JSON.parse(cleanedContent)
          
          // Validate and clean the parsed data
          const validatedData = parsedCostData.map((item: any) => ({
            Agent: item.Agent || 'Unknown',
            'Cost ($)': typeof item['Cost ($)'] === 'number' && !isNaN(item['Cost ($)']) ? item['Cost ($)'] : 0,
            'Prompt Tokens': typeof item['Prompt Tokens'] === 'number' && !isNaN(item['Prompt Tokens']) ? item['Prompt Tokens'] : 0,
            'Completion Tokens': typeof item['Completion Tokens'] === 'number' && !isNaN(item['Completion Tokens']) ? item['Completion Tokens'] : 0,
            'Total Tokens': typeof item['Total Tokens'] === 'number' && !isNaN(item['Total Tokens']) ? item['Total Tokens'] : 0,
            Model: item.Model || 'N/A'
          }))
          
          console.log('Cost data parsed and validated successfully:', validatedData)
          setCostData(validatedData)
          setLastCostUpdate(new Date())
          setCostLoadError(null)
          
        } catch (parseError) {
          throw new Error(`Failed to parse cost data: ${parseError}`)
        }
      } else {
        throw new Error('Cost file is empty')
      }

    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error'
      console.error('Error loading cost data:', errorMessage)
      
      // Retry if we haven't exceeded max attempts and it's a retryable error
      if (retryAttempt < maxRetries - 1 && 
          (errorMessage.includes('not found') || 
           errorMessage.includes('still be generating') || 
           errorMessage.includes('empty'))) {
        
        console.log(`Retrying in ${retryDelays[retryAttempt]}ms...`)
        setTimeout(() => {
          loadCostData(retryAttempt + 1)
        }, retryDelays[retryAttempt])
        return
      }
      
      // Final failure
      setCostLoadError(errorMessage)
      setIsLoadingCost(false)
    }
    
    if (retryAttempt === 0 || costData.length > 0) {
      setIsLoadingCost(false)
    }
  }

  // Load cost data when results become available (task completion)
  useEffect(() => {
    if (results?.work_dir) {
      console.log('Results available, loading cost data...')
      
      // Reset states for new task
      setCostData([])
      setCostLoadError(null)
      setCostLoadAttempts(0)
      setLastCostUpdate(null)
      
      // Longer delay to ensure cost file is written (3 seconds)
      const timer = setTimeout(() => {
        loadCostData()
      }, 3000)
      
      return () => clearTimeout(timer)
    }
  }, [results?.work_dir])

  // Extract total cost from cost data
  const getTotalCost = () => {
    if (costData.length === 0) return null
    
    const totalRow = costData.find((row) => row.Agent === 'Total')
    if (totalRow && totalRow['Cost ($)'] !== undefined) {
      return totalRow['Cost ($)']
    }
    return null
  }

  const totalCost = getTotalCost()

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
      <div className="grid grid-cols-3 gap-4">
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

        <div className="bg-black/20 rounded-lg p-4">
          <div className="flex items-center space-x-2 mb-2">
            <DollarSign className="w-4 h-4 text-yellow-400" />
            <h4 className="text-sm font-medium text-gray-300">Total Cost</h4>
          </div>
          <p className="text-2xl font-bold text-yellow-400">
            {isLoadingCost ? (
              <span className="flex items-center space-x-1">
                <span className="animate-spin">⏳</span>
                <span>Loading...</span>
              </span>
            ) : totalCost ? `$${totalCost.toFixed(6)}` : costLoadError ? 'Error' : 'N/A'}
          </p>
          
          {/* Loading state with attempt info */}
          {isLoadingCost && (
            <p className="text-xs text-blue-400 mt-1">
              {costLoadAttempts > 1 
                ? `Retrying... (attempt ${costLoadAttempts}/5)`
                : 'Loading cost data from files...'
              }
            </p>
          )}
          
          {/* Success state */}
          {totalCost && !isLoadingCost && (
            <p className="text-xs text-green-500 mt-1">
              ✓ Cost data loaded {lastCostUpdate && `at ${lastCostUpdate.toLocaleTimeString()}`}
            </p>
          )}
          
          {/* Error state */}
          {costLoadError && !isLoadingCost && (
            <div className="mt-1">
              <p className="text-xs text-red-400">
                ⚠️ {costLoadError}
              </p>
              <button
                onClick={() => loadCostData()}
                className="text-xs text-blue-400 hover:text-blue-300 underline mt-1"
              >
                Try again
              </button>
            </div>
          )}
          
          {/* Default state */}
          {!totalCost && !isLoadingCost && !costLoadError && (
            <p className="text-xs text-gray-500 mt-1">
              Cost data will appear here after task completion
            </p>
          )}
        </div>
      </div>

      {results.work_dir && (
        <div className="bg-black/20 rounded-lg p-4">
          <h4 className="text-sm font-medium text-gray-300 mb-2">Working Directory</h4>
          <p className="text-xs text-gray-400 font-mono">{results.work_dir}</p>
          <p className="text-xs text-gray-500 mt-1">
            Generated files are saved in: chats, codebase, cost, data, time folders
          </p>
        </div>
      )}

      {/* Cost Breakdown Section */}
      {(costData.length > 0 || isLoadingCost || costLoadError) && (
        <div className="bg-black/20 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-sm font-medium text-gray-300">Cost Breakdown</h4>
            <div className="flex items-center space-x-2">
              {lastCostUpdate && !isLoadingCost && (
                <span className="text-xs text-gray-500">
                  Updated {lastCostUpdate.toLocaleTimeString()}
                </span>
              )}
              <button
                onClick={() => loadCostData()}
                disabled={isLoadingCost}
                className="px-2 py-1 text-xs bg-blue-500/20 hover:bg-blue-500/30 text-blue-400 rounded border border-blue-500/30 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isLoadingCost ? (costLoadAttempts > 1 ? `Retrying (${costLoadAttempts}/5)` : 'Loading...') : 'Refresh'}
              </button>
            </div>
          </div>
          {/* Loading state */}
          {isLoadingCost ? (
            <div className="text-center py-8">
              <div className="flex items-center justify-center space-x-2 text-blue-400 mb-2">
                <span className="animate-spin">⏳</span>
                <span>Loading cost breakdown...</span>
              </div>
              <p className="text-xs text-gray-500">
                {costLoadAttempts > 1 
                  ? `This may take a moment. Retrying (attempt ${costLoadAttempts}/5)...`
                  : 'Fetching cost data from CMBAgent files...'
                }
              </p>
            </div>
          ) : costLoadError ? (
            /* Error state */
            <div className="text-center py-8">
              <div className="text-red-400 mb-2">⚠️ Failed to Load Cost Data</div>
              <p className="text-xs text-gray-400 mb-3">{costLoadError}</p>
              <button
                onClick={() => loadCostData()}
                className="px-3 py-1 text-xs bg-blue-500/20 hover:bg-blue-500/30 text-blue-400 rounded border border-blue-500/30"
              >
                Retry Loading Cost Data
              </button>
            </div>
          ) : (
            /* Success state - show the table */
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-white/10">
                    <th className="text-left pb-2 text-gray-400">Agent</th>
                    <th className="text-right pb-2 text-gray-400">Cost ($)</th>
                    <th className="text-right pb-2 text-gray-400">Prompt Tokens</th>
                    <th className="text-right pb-2 text-gray-400">Completion Tokens</th>
                    <th className="text-right pb-2 text-gray-400">Total Tokens</th>
                    <th className="text-left pb-2 text-gray-400">Model</th>
                  </tr>
                </thead>
                <tbody>
                  {costData
                    .filter((row) => row.Agent !== 'Total')
                    .map((row, index) => (
                      <tr key={index} className="border-b border-white/5">
                        <td className="py-1 text-gray-300">{row.Agent}</td>
                        <td className="py-1 text-right text-yellow-400">${row['Cost ($)']?.toFixed(6) || '0.000000'}</td>
                        <td className="py-1 text-right text-gray-300">{row['Prompt Tokens']?.toLocaleString() || '0'}</td>
                        <td className="py-1 text-right text-gray-300">{row['Completion Tokens']?.toLocaleString() || '0'}</td>
                        <td className="py-1 text-right text-gray-300">{row['Total Tokens']?.toLocaleString() || '0'}</td>
                        <td className="py-1 text-gray-300">{row.Model || 'N/A'}</td>
                      </tr>
                    ))}
                  {/* Total Row */}
                  {totalCost && (
                    <tr className="border-t-2 border-white/20 bg-white/5">
                      <td className="py-2 font-semibold text-white">Total</td>
                      <td className="py-2 text-right font-semibold text-yellow-400">${totalCost.toFixed(6)}</td>
                      <td className="py-2 text-right font-semibold text-white">
                        {(() => {
                          const totalRow = costData.find((row) => row.Agent === 'Total')
                          return totalRow?.['Prompt Tokens']?.toLocaleString() || '0'
                        })()}
                      </td>
                      <td className="py-2 text-right font-semibold text-white">
                        {(() => {
                          const totalRow = costData.find((row) => row.Agent === 'Total')
                          return totalRow?.['Completion Tokens']?.toLocaleString() || '0'
                        })()}
                      </td>
                      <td className="py-2 text-right font-semibold text-white">
                        {(() => {
                          const totalRow = costData.find((row) => row.Agent === 'Total')
                          return totalRow?.['Total Tokens']?.toLocaleString() || '0'
                        })()}
                      </td>
                      <td className="py-2 font-semibold text-white">-</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
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
