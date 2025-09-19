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

interface OCRCostData {
  total_pages_processed: number
  total_cost_usd: number
  cost_per_page: number
  entries: {
    filename: string
    pages_processed: number
    doc_size_bytes: number
    cost_usd: number
    cost_per_page: number
    timestamp: number
  }[]
  last_updated: number
}

interface EnhanceInputCostData {
  ocr_cost: OCRCostData | null
  summarization_costs: CostData[]
  total_cost: number
  breakdown: {
    ocr_cost_usd: number
    summarization_cost_usd: number
  }
}

interface ArxivMetadata {
  urls_found: string[]
  downloads_attempted: number
  downloads_successful: number
  downloads_failed: number
  downloads_skipped: number
  downloaded_files: string[]
  failed_downloads: { url: string, error: string }[]
  output_directory: string
  download_timestamp: number
  download_date: string
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
    mode?: string  // Add mode to identify OCR vs other modes
  } | null
}

export default function ResultDisplay({ results }: ResultDisplayProps) {
  const [activeTab, setActiveTab] = useState('summary')
  const [costData, setCostData] = useState<CostData[]>([])
  const [ocrCostData, setOcrCostData] = useState<OCRCostData | null>(null)
  const [enhanceInputCostData, setEnhanceInputCostData] = useState<EnhanceInputCostData | null>(null)
  const [arxivMetadata, setArxivMetadata] = useState<ArxivMetadata | null>(null)
  const [isLoadingCost, setIsLoadingCost] = useState(false)
  const [isLoadingArxiv, setIsLoadingArxiv] = useState(false)
  const [costLoadError, setCostLoadError] = useState<string | null>(null)
  const [arxivLoadError, setArxivLoadError] = useState<string | null>(null)
  const [costLoadAttempts, setCostLoadAttempts] = useState(0)
  const [lastCostUpdate, setLastCostUpdate] = useState<Date | null>(null)
  const [lastArxivUpdate, setLastArxivUpdate] = useState<Date | null>(null)
  
  // Check mode
  const isOCRMode = results?.mode === 'ocr'
  const isArxivMode = results?.mode === 'arxiv'
  const isEnhanceInputMode = results?.mode === 'enhance-input'

  // Function to load OCR cost data
  const loadOCRCostData = async (retryAttempt = 0): Promise<void> => {
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
    
    console.log(`Loading OCR cost data from: ${results.work_dir} (attempt ${retryAttempt + 1}/${maxRetries})`)

    try {
      const ocrCostPath = `${results.work_dir}/ocr_cost.json`
      const contentResponse = await fetch(`/api/files/content?path=${encodeURIComponent(ocrCostPath)}`)
      
      if (!contentResponse.ok) {
        if (contentResponse.status === 404) {
          throw new Error('OCR cost file not found - OCR processing may still be in progress')
        }
        throw new Error(`Failed to load OCR cost file: ${contentResponse.status}`)
      }

      const contentData = await contentResponse.json()
      if (contentData.content) {
        const parsedData: OCRCostData = JSON.parse(contentData.content)
        setOcrCostData(parsedData)
        setLastCostUpdate(new Date())
        setCostLoadError(null)
        console.log('OCR cost data loaded successfully:', parsedData)
      } else {
        throw new Error('OCR cost file is empty')
      }

    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error'
      console.error('Error loading OCR cost data:', errorMessage)
      
      // Retry if we haven't exceeded max attempts and it's a retryable error
      if (retryAttempt < maxRetries - 1 && 
          (errorMessage.includes('not found') || 
           errorMessage.includes('still be') || 
           errorMessage.includes('empty'))) {
        
        console.log(`Retrying OCR cost load in ${retryDelays[retryAttempt]}ms...`)
        setTimeout(() => {
          loadOCRCostData(retryAttempt + 1)
        }, retryDelays[retryAttempt])
        return
      }
      
      // Final failure
      setCostLoadError(errorMessage)
      setIsLoadingCost(false)
    }
    
    if (retryAttempt === 0 || ocrCostData) {
      setIsLoadingCost(false)
    }
  }

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
      // Check if this is Planning & Control mode (has planning/control subfolders) or One Shot mode
      let costFiles: any[] = []
      let allCostData: any[] = []
      
      // Try to detect mode by checking for planning/control folders
      const planningDir = `${results.work_dir}/planning/cost`
      const controlDir = `${results.work_dir}/control/cost`
      const oneShötCostDir = `${results.work_dir}/cost`
      
      // Check for planning directory first
      let isPlanningControlMode = false
      try {
        const planningResponse = await fetch(`/api/files/list?path=${encodeURIComponent(planningDir)}`)
        if (planningResponse.ok) {
          isPlanningControlMode = true
          console.log('Detected Planning & Control mode')
        }
      } catch (e) {
        // Planning folder doesn't exist, try One Shot mode
      }

      if (isPlanningControlMode) {
        // Planning & Control mode - collect from multiple folders
        const costDirs = [planningDir, controlDir]
        
        for (const costDir of costDirs) {
          console.log('Checking cost directory:', costDir)
          
          try {
            const listResponse = await fetch(`/api/files/list?path=${encodeURIComponent(costDir)}`)
            if (listResponse.ok) {
              const listData = await listResponse.json()
              const dirCostFiles = listData.items?.filter((file: any) => 
                file.name.startsWith('cost_report_') && file.name.endsWith('.json')
              ) || []
              
              // Read all cost files from this directory
              for (const costFile of dirCostFiles) {
                const contentResponse = await fetch(`/api/files/content?path=${encodeURIComponent(costFile.path)}`)
                if (contentResponse.ok) {
                  const contentData = await contentResponse.json()
                  if (contentData.content) {
                    // Clean and parse the JSON
                    let cleanedContent = contentData.content.replace(/:\s*NaN/g, ': null')
                    const parsedData = JSON.parse(cleanedContent)
                    
                    // Filter out Total rows (we'll calculate our own)
                    const nonTotalData = parsedData.filter((item: any) => item.Agent !== 'Total')
                    allCostData.push(...nonTotalData)
                  }
                }
              }
            }
          } catch (e) {
            console.log(`Could not access ${costDir}:`, e)
          }
        }
        
        if (allCostData.length === 0) {
          throw new Error('No cost report files found in planning/control directories - task may still be generating reports')
        }
        
      } else {
        // One Shot mode - single cost directory
        console.log('Detected One Shot mode')
        const listResponse = await fetch(`/api/files/list?path=${encodeURIComponent(oneShötCostDir)}`)
        
        if (!listResponse.ok) {
          if (listResponse.status === 404) {
            throw new Error('Cost directory not found - task may still be generating cost reports')
          }
          throw new Error(`Failed to list cost directory: ${listResponse.status}`)
        }

        const listData = await listResponse.json()
        const costFiles = listData.items?.filter((file: any) => 
          file.name.startsWith('cost_report_') && file.name.endsWith('.json')
        ) || []

        if (costFiles.length === 0) {
          throw new Error('No cost report files found - task may still be generating reports')
        }

        // Get the most recent cost file
        costFiles.sort((a: any, b: any) => (b.modified || 0) - (a.modified || 0))
        const latestCostFile = costFiles[0]

        const contentResponse = await fetch(`/api/files/content?path=${encodeURIComponent(latestCostFile.path)}`)
        if (!contentResponse.ok) {
          throw new Error(`Failed to read cost file: ${contentResponse.status}`)
        }

        const contentData = await contentResponse.json()
        if (contentData.content) {
          let cleanedContent = contentData.content.replace(/:\s*NaN/g, ': null')
          const parsedData = JSON.parse(cleanedContent)
          allCostData = parsedData
        }
      }

      // Process and validate all cost data
      const processedData = allCostData.map((item: any) => ({
        Agent: item.Agent || 'Unknown',
        'Cost ($)': typeof item['Cost ($)'] === 'number' && !isNaN(item['Cost ($)']) ? item['Cost ($)'] : 0,
        'Prompt Tokens': typeof item['Prompt Tokens'] === 'number' && !isNaN(item['Prompt Tokens']) ? item['Prompt Tokens'] : 0,
        'Completion Tokens': typeof item['Completion Tokens'] === 'number' && !isNaN(item['Completion Tokens']) ? item['Completion Tokens'] : 0,
        'Total Tokens': typeof item['Total Tokens'] === 'number' && !isNaN(item['Total Tokens']) ? item['Total Tokens'] : 0,
        Model: item.Model || 'N/A'
      }))

      // For Planning & Control mode, calculate totals across all steps
      if (isPlanningControlMode) {
        // Aggregate data by agent (combining costs from different steps)
        const agentTotals = new Map()
        
        processedData.forEach((item: any) => {
          if (item.Agent === 'Total') return // Skip existing total rows
          
          const key = item.Agent
          if (agentTotals.has(key)) {
            const existing = agentTotals.get(key)
            existing['Cost ($)'] += item['Cost ($)']
            existing['Prompt Tokens'] += item['Prompt Tokens']
            existing['Completion Tokens'] += item['Completion Tokens']
            existing['Total Tokens'] += item['Total Tokens']
          } else {
            agentTotals.set(key, { ...item })
          }
        })

        const aggregatedData = Array.from(agentTotals.values())
        
        // Calculate overall totals
        const totals = aggregatedData.reduce((acc: any, item: any) => ({
          'Cost ($)': acc['Cost ($)'] + item['Cost ($)'],
          'Prompt Tokens': acc['Prompt Tokens'] + item['Prompt Tokens'],
          'Completion Tokens': acc['Completion Tokens'] + item['Completion Tokens'],
          'Total Tokens': acc['Total Tokens'] + item['Total Tokens']
        }), { 'Cost ($)': 0, 'Prompt Tokens': 0, 'Completion Tokens': 0, 'Total Tokens': 0 })

        // Add total row
        aggregatedData.push({
          Agent: 'Total',
          'Cost ($)': totals['Cost ($)'],
          'Prompt Tokens': totals['Prompt Tokens'],
          'Completion Tokens': totals['Completion Tokens'],
          'Total Tokens': totals['Total Tokens'],
          Model: null
        })

        setCostData(aggregatedData)
      } else {
        setCostData(processedData)
      }
      
      setLastCostUpdate(new Date())
      setCostLoadError(null)
      console.log('Cost data loaded successfully:', isPlanningControlMode ? 'Planning & Control' : 'One Shot')

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

  // Function to load arXiv metadata
  const loadArxivMetadata = async (): Promise<void> => {
    if (!results?.work_dir) {
      console.log('No work directory available for arXiv metadata')
      setArxivLoadError('No work directory available')
      return
    }

    setIsLoadingArxiv(true)
    setArxivLoadError(null)
    
    console.log(`Loading arXiv metadata from: ${results.work_dir}`)

    try {
      const arxivMetadataPath = `${results.work_dir}/docs/arxiv_download_metadata.json`
      const contentResponse = await fetch(`/api/files/content?path=${encodeURIComponent(arxivMetadataPath)}`)
      
      if (!contentResponse.ok) {
        if (contentResponse.status === 404) {
          throw new Error('arXiv metadata file not found - processing may still be in progress')
        }
        throw new Error(`Failed to load arXiv metadata file: ${contentResponse.status}`)
      }

      const contentData = await contentResponse.json()
      if (contentData.content) {
        const parsedData: ArxivMetadata = JSON.parse(contentData.content)
        setArxivMetadata(parsedData)
        setLastArxivUpdate(new Date())
        setArxivLoadError(null)
        console.log('arXiv metadata loaded successfully:', parsedData)
      } else {
        throw new Error('arXiv metadata file is empty')
      }

    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error'
      console.error('Error loading arXiv metadata:', errorMessage)
      setArxivLoadError(errorMessage)
    }
    
    setIsLoadingArxiv(false)
  }

  // Function to load enhance-input cost data (OCR + summarization costs)
  const loadEnhanceInputCostData = async (retryAttempt = 0): Promise<void> => {
    if (!results?.work_dir) {
      console.log('No work directory available for enhance-input costs')
      setCostLoadError('No work directory available')
      return
    }

    const maxRetries = 5
    const retryDelays = [2000, 3000, 5000, 8000, 12000]

    setIsLoadingCost(true)
    setCostLoadError(null)
    setCostLoadAttempts(retryAttempt + 1)
    
    console.log(`Loading enhance-input cost data from: ${results.work_dir} (attempt ${retryAttempt + 1}/${maxRetries})`)

    try {
      let totalCost = 0
      let ocrCostData: OCRCostData | null = null
      let summarizationCosts: CostData[] = []
      let ocrCostUsd = 0
      let summarizationCostUsd = 0

      // 1. Load OCR cost data
      try {
        const ocrCostPath = `${results.work_dir}/ocr_cost.json`
        const ocrContentResponse = await fetch(`/api/files/content?path=${encodeURIComponent(ocrCostPath)}`)
        
        if (ocrContentResponse.ok) {
          const ocrContentData = await ocrContentResponse.json()
          if (ocrContentData.content) {
            ocrCostData = JSON.parse(ocrContentData.content)
            ocrCostUsd = ocrCostData?.total_cost_usd || 0
            totalCost += ocrCostUsd
            console.log('OCR cost data loaded:', ocrCostUsd)
          }
        } else {
          console.log('OCR cost file not found, may not have OCR costs')
        }
      } catch (error) {
        console.log('Failed to load OCR costs:', error)
      }

      // 2. Load summarization cost data from summaries subdirectories
      try {
        const summariesDir = `${results.work_dir}/summaries`
        const summariesListResponse = await fetch(`/api/files/list?path=${encodeURIComponent(summariesDir)}`)
        
        if (summariesListResponse.ok) {
          const summariesListData = await summariesListResponse.json()
          const docDirs = summariesListData.items?.filter((item: any) => 
            item.type === 'directory' && item.name.startsWith('doc_')
          ) || []

          for (const docDir of docDirs) {
            const costDir = `${docDir.path}/cost`
            const costListResponse = await fetch(`/api/files/list?path=${encodeURIComponent(costDir)}`)
            
            if (costListResponse.ok) {
              const costListData = await costListResponse.json()
              const costFiles = costListData.items?.filter((file: any) => 
                file.name.startsWith('cost_report_') && file.name.endsWith('.json')
              ) || []

              for (const costFile of costFiles) {
                const contentResponse = await fetch(`/api/files/content?path=${encodeURIComponent(costFile.path)}`)
                if (contentResponse.ok) {
                  const contentData = await contentResponse.json()
                  if (contentData.content) {
                    let cleanedContent = contentData.content.replace(/:\s*NaN/g, ': null')
                    const parsedData = JSON.parse(cleanedContent)
                    
                    // Filter out Total rows and add to summarization costs
                    const nonTotalData = parsedData.filter((item: any) => item.Agent !== 'Total')
                    summarizationCosts.push(...nonTotalData)
                  }
                }
              }
            }
          }

          // Calculate summarization total cost
          summarizationCostUsd = summarizationCosts.reduce((sum, item) => 
            sum + (typeof item['Cost ($)'] === 'number' && !isNaN(item['Cost ($)']) ? item['Cost ($)'] : 0), 0)
          totalCost += summarizationCostUsd
          console.log('Summarization cost data loaded:', summarizationCostUsd)
        } else {
          console.log('Summaries directory not found, may not have summarization costs')
        }
      } catch (error) {
        console.log('Failed to load summarization costs:', error)
      }

      if (ocrCostData || summarizationCosts.length > 0) {
        const enhanceInputData: EnhanceInputCostData = {
          ocr_cost: ocrCostData,
          summarization_costs: summarizationCosts,
          total_cost: totalCost,
          breakdown: {
            ocr_cost_usd: ocrCostUsd,
            summarization_cost_usd: summarizationCostUsd
          }
        }
        
        setEnhanceInputCostData(enhanceInputData)
        setLastCostUpdate(new Date())
        setCostLoadError(null)
        console.log('Enhance-input cost data loaded successfully:', enhanceInputData)
      } else {
        throw new Error('No cost data found - processing may still be in progress')
      }

    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error'
      console.error('Error loading enhance-input cost data:', errorMessage)
      
      // Retry if we haven't exceeded max attempts and it's a retryable error
      if (retryAttempt < maxRetries - 1 && 
          (errorMessage.includes('not found') || 
           errorMessage.includes('still be') || 
           errorMessage.includes('empty'))) {
        
        console.log(`Retrying enhance-input cost load in ${retryDelays[retryAttempt]}ms...`)
        setTimeout(() => {
          loadEnhanceInputCostData(retryAttempt + 1)
        }, retryDelays[retryAttempt])
        return
      }
      
      // Final failure
      setCostLoadError(errorMessage)
      setIsLoadingCost(false)
    }
    
    if (retryAttempt === 0 || enhanceInputCostData) {
      setIsLoadingCost(false)
    }
  }

  // Load cost data when results become available (task completion)
  useEffect(() => {
    if (results?.work_dir) {
      console.log('Results available, loading cost data...', 'Mode:', results.mode)
      
      // Reset states for new task
      setCostData([])
      setOcrCostData(null)
      setEnhanceInputCostData(null)
      setArxivMetadata(null)
      setCostLoadError(null)
      setArxivLoadError(null)
      setCostLoadAttempts(0)
      setLastCostUpdate(null)
      setLastArxivUpdate(null)
      
      // Longer delay to ensure files are written (3 seconds)
      const timer = setTimeout(() => {
        if (isEnhanceInputMode) {
          loadEnhanceInputCostData()
        } else if (isOCRMode) {
          loadOCRCostData()
        } else if (isArxivMode) {
          loadArxivMetadata()
        } else {
          loadCostData()
        }
      }, 3000)
      
      return () => clearTimeout(timer)
    }
  }, [results?.work_dir, results?.mode])

  // Extract total cost from cost data
  const getTotalCost = () => {
    // arXiv mode has no cost
    if (isArxivMode) {
      return null
    }
    
    if (isEnhanceInputMode) {
      return enhanceInputCostData?.total_cost || null
    }
    
    if (isOCRMode) {
      return ocrCostData?.total_cost_usd || null
    }
    
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
      <div className={`grid gap-4 ${isArxivMode ? 'grid-cols-2' : 'grid-cols-3'}`}>
        <div className="bg-black/20 rounded-lg p-4">
          <h4 className="text-sm font-medium text-gray-300 mb-2">Execution Time</h4>
          <p className="text-2xl font-bold text-green-400">
            {results.execution_time ? `${results.execution_time.toFixed(2)}s` : 'N/A'}
          </p>
        </div>

        {/* Cost card - hidden for arXiv mode */}
        {!isArxivMode && (
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
        )}

        {/* arXiv Downloads card - shown for arXiv mode */}
        {isArxivMode && (
          <div className="bg-black/20 rounded-lg p-4">
            <div className="flex items-center space-x-2 mb-2">
              <FileText className="w-4 h-4 text-blue-400" />
              <h4 className="text-sm font-medium text-gray-300">Downloads</h4>
            </div>
            <p className="text-2xl font-bold text-blue-400">
              {isLoadingArxiv ? (
                <span className="flex items-center space-x-1">
                  <span className="animate-spin">⏳</span>
                  <span>Loading...</span>
                </span>
              ) : arxivMetadata ? `${arxivMetadata.downloads_successful}` : arxivLoadError ? 'Error' : 'N/A'}
            </p>
            
            {/* Success state */}
            {arxivMetadata && !isLoadingArxiv && (
              <p className="text-xs text-green-500 mt-1">
                ✓ Downloaded {arxivMetadata.downloads_successful}/{arxivMetadata.downloads_attempted} papers
              </p>
            )}
            
            {/* Error state */}
            {arxivLoadError && !isLoadingArxiv && (
              <div className="mt-1">
                <p className="text-xs text-red-400">
                  ⚠️ {arxivLoadError}
                </p>
                <button
                  onClick={() => loadArxivMetadata()}
                  className="text-xs text-blue-400 hover:text-blue-300 underline mt-1"
                >
                  Try again
                </button>
              </div>
            )}
            
            {/* Default state */}
            {!arxivMetadata && !isLoadingArxiv && !arxivLoadError && (
              <p className="text-xs text-gray-500 mt-1">
                Download data will appear here after completion
              </p>
            )}
          </div>
        )}
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

      {/* Cost Breakdown Section - hidden for arXiv mode */}
      {!isArxivMode && (costData.length > 0 || ocrCostData || enhanceInputCostData || isLoadingCost || costLoadError) && (
        <div className="bg-black/20 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-sm font-medium text-gray-300">
              {isEnhanceInputMode ? 'Enhance Input Cost Breakdown' : isOCRMode ? 'OCR Cost Breakdown' : 'Cost Breakdown'}
            </h4>
            <div className="flex items-center space-x-2">
              {lastCostUpdate && !isLoadingCost && (
                <span className="text-xs text-gray-500">
                  Updated {lastCostUpdate.toLocaleTimeString()}
                </span>
              )}
              <button
                onClick={() => isEnhanceInputMode ? loadEnhanceInputCostData() : isOCRMode ? loadOCRCostData() : loadCostData()}
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
          ) : isEnhanceInputMode && enhanceInputCostData ? (
            /* Enhance Input Success state - show combined OCR and summarization costs */
            <div className="space-y-4">
              {/* Cost Breakdown Summary */}
              <div className="grid grid-cols-2 gap-4">
                <div className="bg-black/20 rounded-lg p-3">
                  <h5 className="text-xs font-medium text-gray-400 mb-1">OCR Cost</h5>
                  <p className="text-lg font-bold text-blue-400">${enhanceInputCostData.breakdown.ocr_cost_usd.toFixed(6)}</p>
                </div>
                <div className="bg-black/20 rounded-lg p-3">
                  <h5 className="text-xs font-medium text-gray-400 mb-1">Summarization Cost</h5>
                  <p className="text-lg font-bold text-green-400">${enhanceInputCostData.breakdown.summarization_cost_usd.toFixed(6)}</p>
                </div>
              </div>

              {/* OCR Details if available */}
              {enhanceInputCostData.ocr_cost && (
                <div>
                  <h5 className="text-sm font-medium text-gray-300 mb-2">OCR Processing Details</h5>
                  <div className="grid grid-cols-3 gap-4 mb-3">
                    <div className="bg-black/10 rounded-lg p-2">
                      <p className="text-xs text-gray-400">Pages Processed</p>
                      <p className="text-sm font-bold text-blue-400">{enhanceInputCostData.ocr_cost.total_pages_processed}</p>
                    </div>
                    <div className="bg-black/10 rounded-lg p-2">
                      <p className="text-xs text-gray-400">Files Processed</p>
                      <p className="text-sm font-bold text-green-400">{enhanceInputCostData.ocr_cost.entries.length}</p>
                    </div>
                    <div className="bg-black/10 rounded-lg p-2">
                      <p className="text-xs text-gray-400">Cost per Page</p>
                      <p className="text-sm font-bold text-orange-400">${enhanceInputCostData.ocr_cost.cost_per_page.toFixed(4)}</p>
                    </div>
                  </div>
                </div>
              )}

              {/* Summarization Details if available */}
              {enhanceInputCostData.summarization_costs.length > 0 && (
                <div>
                  <h5 className="text-sm font-medium text-gray-300 mb-2">Summarization Processing Details</h5>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-white/10">
                          <th className="text-left pb-2 text-gray-400">Agent</th>
                          <th className="text-right pb-2 text-gray-400">Cost ($)</th>
                          <th className="text-right pb-2 text-gray-400">Prompt Tokens</th>
                          <th className="text-right pb-2 text-gray-400">Completion Tokens</th>
                          <th className="text-left pb-2 text-gray-400">Model</th>
                        </tr>
                      </thead>
                      <tbody>
                        {enhanceInputCostData.summarization_costs.map((row, index) => (
                          <tr key={index} className="border-b border-white/5">
                            <td className="py-1 text-gray-300">{row.Agent}</td>
                            <td className="py-1 text-right text-yellow-400">${row['Cost ($)']?.toFixed(6) || '0.000000'}</td>
                            <td className="py-1 text-right text-gray-300">{row['Prompt Tokens']?.toLocaleString() || '0'}</td>
                            <td className="py-1 text-right text-gray-300">{row['Completion Tokens']?.toLocaleString() || '0'}</td>
                            <td className="py-1 text-gray-300">{row.Model || 'N/A'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Total Summary */}
              <div className="bg-white/5 rounded-lg p-3 border-t-2 border-yellow-400/30">
                <div className="flex justify-between items-center">
                  <span className="text-sm font-semibold text-white">Total Cost</span>
                  <span className="text-lg font-bold text-yellow-400">${enhanceInputCostData.total_cost.toFixed(6)}</span>
                </div>
                <div className="grid grid-cols-2 gap-4 mt-2 text-xs text-gray-400">
                  <div>OCR: ${enhanceInputCostData.breakdown.ocr_cost_usd.toFixed(6)}</div>
                  <div>Summarization: ${enhanceInputCostData.breakdown.summarization_cost_usd.toFixed(6)}</div>
                </div>
              </div>
            </div>
          ) : isOCRMode && ocrCostData ? (
            /* OCR Success state - show OCR specific table */
            <div className="space-y-4">
              {/* OCR Summary */}
              <div className="grid grid-cols-3 gap-4">
                <div className="bg-black/20 rounded-lg p-3">
                  <h5 className="text-xs font-medium text-gray-400 mb-1">Total Pages</h5>
                  <p className="text-lg font-bold text-blue-400">{ocrCostData.total_pages_processed}</p>
                </div>
                <div className="bg-black/20 rounded-lg p-3">
                  <h5 className="text-xs font-medium text-gray-400 mb-1">Files Processed</h5>
                  <p className="text-lg font-bold text-green-400">{ocrCostData.entries.length}</p>
                </div>
                <div className="bg-black/20 rounded-lg p-3">
                  <h5 className="text-xs font-medium text-gray-400 mb-1">Cost per Page</h5>
                  <p className="text-lg font-bold text-orange-400">${ocrCostData.cost_per_page.toFixed(3)}</p>
                </div>
              </div>
              
              {/* OCR Files Table */}
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-white/10">
                      <th className="text-left pb-2 text-gray-400">Filename</th>
                      <th className="text-right pb-2 text-gray-400">Pages</th>
                      <th className="text-right pb-2 text-gray-400">Size (KB)</th>
                      <th className="text-right pb-2 text-gray-400">Cost ($)</th>
                      <th className="text-right pb-2 text-gray-400">Processed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ocrCostData.entries.map((entry, index) => (
                      <tr key={index} className="border-b border-white/5">
                        <td className="py-1 text-gray-300 max-w-[200px] truncate" title={entry.filename}>
                          {entry.filename}
                        </td>
                        <td className="py-1 text-right text-blue-400">{entry.pages_processed}</td>
                        <td className="py-1 text-right text-gray-300">{(entry.doc_size_bytes / 1024).toFixed(1)}</td>
                        <td className="py-1 text-right text-yellow-400">${entry.cost_usd.toFixed(3)}</td>
                        <td className="py-1 text-right text-gray-300">
                          {new Date(entry.timestamp * 1000).toLocaleTimeString()}
                        </td>
                      </tr>
                    ))}
                    {/* Total Row for OCR */}
                    <tr className="border-t-2 border-white/20 bg-white/5">
                      <td className="py-2 font-semibold text-white">Total</td>
                      <td className="py-2 text-right font-semibold text-blue-400">{ocrCostData.total_pages_processed}</td>
                      <td className="py-2 text-right font-semibold text-white">
                        {(ocrCostData.entries.reduce((sum, entry) => sum + entry.doc_size_bytes, 0) / 1024).toFixed(1)}
                      </td>
                      <td className="py-2 text-right font-semibold text-yellow-400">${ocrCostData.total_cost_usd.toFixed(3)}</td>
                      <td className="py-2 text-right font-semibold text-white">-</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          ) : !isOCRMode ? (
            /* LLM Success state - show the standard LLM table */
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
          ) : null}
        </div>
      )}

      {/* arXiv Metadata Section - shown only for arXiv mode */}
      {isArxivMode && (arxivMetadata || isLoadingArxiv || arxivLoadError) && (
        <div className="bg-black/20 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-sm font-medium text-gray-300">arXiv Download Summary</h4>
            <div className="flex items-center space-x-2">
              {lastArxivUpdate && !isLoadingArxiv && (
                <span className="text-xs text-gray-500">
                  Updated {lastArxivUpdate.toLocaleTimeString()}
                </span>
              )}
              <button
                onClick={() => loadArxivMetadata()}
                disabled={isLoadingArxiv}
                className="px-2 py-1 text-xs bg-blue-500/20 hover:bg-blue-500/30 text-blue-400 rounded border border-blue-500/30 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isLoadingArxiv ? 'Loading...' : 'Refresh'}
              </button>
            </div>
          </div>

          {/* Loading state */}
          {isLoadingArxiv ? (
            <div className="text-center py-8">
              <div className="flex items-center justify-center space-x-2 text-blue-400 mb-2">
                <span className="animate-spin">⏳</span>
                <span>Loading arXiv metadata...</span>
              </div>
            </div>
          ) : arxivLoadError ? (
            /* Error state */
            <div className="text-center py-8">
              <div className="text-red-400 mb-2">⚠️ Failed to Load arXiv Data</div>
              <p className="text-xs text-gray-400 mb-3">{arxivLoadError}</p>
              <button
                onClick={() => loadArxivMetadata()}
                className="px-3 py-1 text-xs bg-blue-500/20 hover:bg-blue-500/30 text-blue-400 rounded border border-blue-500/30"
              >
                Retry Loading arXiv Data
              </button>
            </div>
          ) : arxivMetadata ? (
            /* Success state - show arXiv metadata */
            <div className="space-y-4">
              {/* Summary statistics */}
              <div className="grid grid-cols-4 gap-4">
                <div className="text-center">
                  <p className="text-2xl font-bold text-blue-400">{arxivMetadata.urls_found.length}</p>
                  <p className="text-xs text-gray-400">URLs Found</p>
                </div>
                <div className="text-center">
                  <p className="text-2xl font-bold text-green-400">{arxivMetadata.downloads_successful}</p>
                  <p className="text-xs text-gray-400">Downloaded</p>
                </div>
                <div className="text-center">
                  <p className="text-2xl font-bold text-yellow-400">{arxivMetadata.downloads_skipped}</p>
                  <p className="text-xs text-gray-400">Skipped</p>
                </div>
                <div className="text-center">
                  <p className="text-2xl font-bold text-red-400">{arxivMetadata.downloads_failed}</p>
                  <p className="text-xs text-gray-400">Failed</p>
                </div>
              </div>

              {/* Downloaded files */}
              {arxivMetadata.downloaded_files.length > 0 && (
                <div>
                  <h5 className="text-sm font-medium text-gray-300 mb-2">Downloaded Papers</h5>
                  <div className="space-y-1">
                    {arxivMetadata.downloaded_files.map((file, index) => (
                      <div key={index} className="flex items-center text-xs">
                        <span className="text-green-400 mr-2">✓</span>
                        <span className="text-gray-300 font-mono">{file.split('/').pop()}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Failed downloads */}
              {arxivMetadata.failed_downloads.length > 0 && (
                <div>
                  <h5 className="text-sm font-medium text-gray-300 mb-2">Failed Downloads</h5>
                  <div className="space-y-1">
                    {arxivMetadata.failed_downloads.map((failure, index) => (
                      <div key={index} className="text-xs">
                        <div className="flex items-center">
                          <span className="text-red-400 mr-2">✗</span>
                          <span className="text-gray-300">{failure.url}</span>
                        </div>
                        <p className="text-red-400 ml-4 text-xs">{failure.error}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* URLs found */}
              <div>
                <h5 className="text-sm font-medium text-gray-300 mb-2">URLs Detected</h5>
                <div className="space-y-1 max-h-32 overflow-y-auto">
                  {arxivMetadata.urls_found.map((url, index) => (
                    <div key={index} className="text-xs">
                      <span className="text-blue-400 font-mono">{url}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Metadata */}
              <div className="text-xs text-gray-500 space-y-1">
                <p>Output Directory: <span className="font-mono">{arxivMetadata.output_directory}</span></p>
                <p>Downloaded: {arxivMetadata.download_date}</p>
              </div>
            </div>
          ) : null}
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
