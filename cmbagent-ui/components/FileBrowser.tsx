'use client'

import { useState, useEffect } from 'react'
import {
  Folder,
  File,
  Image,
  FileText,
  Code,
  BarChart3,
  Clock,
  MessageSquare,
  ArrowLeft,
  RotateCcw,
  Download,
  Eye
} from 'lucide-react'

interface FileItem {
  name: string
  path: string
  type: 'file' | 'directory'
  size?: number
  modified?: number
  mime_type?: string
}

interface DirectoryListing {
  path: string
  items: FileItem[]
  parent?: string
}

interface FileBrowserProps {
  workDir: string
  onFileSelect?: (file: FileItem) => void
}

export default function FileBrowser({ workDir, onFileSelect }: FileBrowserProps) {
  const [currentPath, setCurrentPath] = useState(workDir)
  const [listing, setListing] = useState<DirectoryListing | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedFile, setSelectedFile] = useState<FileItem | null>(null)
  const [fileContent, setFileContent] = useState<string | null>(null)

  const loadDirectory = async (path: string) => {
    setLoading(true)
    setError(null)
    
    try {
      const response = await fetch(`/api/files/list?path=${encodeURIComponent(path)}`)
      if (!response.ok) {
        throw new Error(`Failed to load directory: ${response.statusText}`)
      }
      
      const data: DirectoryListing = await response.json()
      setListing(data)
      setCurrentPath(data.path)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load directory')
    } finally {
      setLoading(false)
    }
  }

  const loadFileContent = async (file: FileItem) => {
    if (file.type !== 'file') return

    // Check if it's an image file
    const isImage = file.mime_type?.startsWith('image/') ||
                   /\.(png|jpg|jpeg|gif|bmp|svg|webp|tiff|tif)$/i.test(file.name)

    if (isImage) {
      // For images, just set the selected file without loading content
      setSelectedFile(file)
      setFileContent(null) // Clear text content for images

      if (onFileSelect) {
        onFileSelect(file)
      }
      return
    }

    // For text files, load content as before
    try {
      const response = await fetch(`/api/files/content?path=${encodeURIComponent(file.path)}`)
      if (!response.ok) {
        throw new Error(`Failed to load file: ${response.statusText}`)
      }

      const data = await response.json()
      setFileContent(data.content)
      setSelectedFile(file)

      if (onFileSelect) {
        onFileSelect(file)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load file')
    }
  }

  useEffect(() => {
    loadDirectory(currentPath)
  }, [currentPath])

  const getFileIcon = (item: FileItem) => {
    if (item.type === 'directory') {
      // Special icons for CMBAgent directories
      switch (item.name) {
        case 'chats': return <MessageSquare className="w-4 h-4 text-blue-400" />
        case 'codebase': return <Code className="w-4 h-4 text-green-400" />
        case 'cost': return <BarChart3 className="w-4 h-4 text-yellow-400" />
        case 'data': return <Folder className="w-4 h-4 text-purple-400" />
        case 'time': return <Clock className="w-4 h-4 text-orange-400" />
        default: return <Folder className="w-4 h-4 text-blue-400" />
      }
    }
    
    // File icons based on extension or mime type
    const ext = item.name.split('.').pop()?.toLowerCase()
    const mimeType = item.mime_type
    
    if (mimeType?.startsWith('image/')) {
      return <Image className="w-4 h-4 text-green-400" />
    } else if (ext === 'py' || ext === 'js' || ext === 'ts' || ext === 'jsx' || ext === 'tsx') {
      return <Code className="w-4 h-4 text-blue-400" />
    } else if (ext === 'json' || ext === 'csv' || ext === 'txt' || ext === 'md') {
      return <FileText className="w-4 h-4 text-gray-400" />
    } else {
      return <File className="w-4 h-4 text-gray-400" />
    }
  }

  const formatFileSize = (bytes?: number) => {
    if (!bytes) return ''
    
    const units = ['B', 'KB', 'MB', 'GB']
    let size = bytes
    let unitIndex = 0
    
    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024
      unitIndex++
    }
    
    return `${size.toFixed(1)} ${units[unitIndex]}`
  }

  const formatDate = (timestamp?: number) => {
    if (!timestamp) return ''
    return new Date(timestamp * 1000).toLocaleString()
  }

  const navigateUp = () => {
    if (listing?.parent) {
      setCurrentPath(listing.parent)
    }
  }

  const navigateToDirectory = (item: FileItem) => {
    if (item.type === 'directory') {
      setCurrentPath(item.path)
    }
  }

  return (
    <div className="h-full flex flex-col bg-black/20 rounded-lg border border-white/10">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-white/10">
        <div className="flex items-center space-x-2">
          <Folder className="w-5 h-5 text-blue-400" />
          <h3 className="text-white font-medium">File Browser</h3>
        </div>
        
        <div className="flex items-center space-x-2">
          <button
            onClick={() => loadDirectory(currentPath)}
            disabled={loading}
            className="p-2 text-gray-400 hover:text-white transition-colors"
            title="Refresh"
          >
            <RotateCcw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Navigation */}
      <div className="flex items-center p-3 bg-black/30 border-b border-white/10">
        <button
          onClick={navigateUp}
          disabled={!listing?.parent || loading}
          className="p-1 text-gray-400 hover:text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed mr-2"
          title="Go up"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        
        <span className="text-sm text-gray-300 font-mono truncate">
          {currentPath}
        </span>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-400"></div>
          </div>
        ) : error ? (
          <div className="flex items-center justify-center h-full text-red-400">
            <p>{error}</p>
          </div>
        ) : (
          <div className="h-full overflow-y-auto">
            {listing?.items.length === 0 ? (
              <div className="flex items-center justify-center h-full text-gray-500">
                <p>Directory is empty</p>
              </div>
            ) : (
              <div className="p-2">
                {listing?.items.map((item, index) => (
                  <div
                    key={index}
                    className={`flex items-center p-2 rounded hover:bg-white/10 cursor-pointer transition-colors ${
                      selectedFile?.path === item.path ? 'bg-blue-600/20' : ''
                    }`}
                    onClick={() => {
                      if (item.type === 'directory') {
                        navigateToDirectory(item)
                      } else {
                        loadFileContent(item)
                      }
                    }}
                  >
                    <div className="flex items-center flex-1 min-w-0">
                      {getFileIcon(item)}
                      <span className="ml-2 text-sm text-white truncate">
                        {item.name}
                      </span>
                    </div>
                    
                    <div className="flex items-center space-x-4 text-xs text-gray-400">
                      {item.type === 'file' && (
                        <span>{formatFileSize(item.size)}</span>
                      )}
                      <span>{formatDate(item.modified)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* File Content Preview */}
      {selectedFile && (
        <div className="border-t border-white/10 p-4">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h4 className="text-sm font-medium text-white">{selectedFile.name}</h4>
              <p className="text-xs text-gray-400">
                {selectedFile.mime_type} • {formatFileSize(selectedFile.size)}
              </p>
            </div>
            <div className="flex items-center space-x-2">
              <button
                onClick={() => {
                  const isImage = selectedFile.mime_type?.startsWith('image/') ||
                                 /\.(png|jpg|jpeg|gif|bmp|svg|webp|tiff|tif)$/i.test(selectedFile.name)

                  if (isImage) {
                    // For images, download directly from the serve-image endpoint
                    const link = document.createElement('a')
                    link.href = `/api/files/serve-image?path=${encodeURIComponent(selectedFile.path)}`
                    link.download = selectedFile.name
                    link.click()
                  } else {
                    // For text files, create blob from content
                    const blob = new Blob([fileContent || ''], { type: 'text/plain' })
                    const url = URL.createObjectURL(blob)
                    const a = document.createElement('a')
                    a.href = url
                    a.download = selectedFile.name
                    a.click()
                    URL.revokeObjectURL(url)
                  }
                }}
                className="p-1 text-gray-400 hover:text-white transition-colors"
                title="Download"
              >
                <Download className="w-3 h-3" />
              </button>
              <button
                onClick={() => {
                  const isImage = selectedFile.mime_type?.startsWith('image/') ||
                                 /\.(png|jpg|jpeg|gif|bmp|svg|webp|tiff|tif)$/i.test(selectedFile.name)

                  if (isImage) {
                    window.open(`/api/files/serve-image?path=${encodeURIComponent(selectedFile.path)}`, '_blank')
                  }
                }}
                className="p-1 text-gray-400 hover:text-white transition-colors"
                title="View in new tab"
              >
                <Eye className="w-3 h-3" />
              </button>
            </div>
          </div>

          {/* Image Preview */}
          {selectedFile.mime_type?.startsWith('image/') || /\.(png|jpg|jpeg|gif|bmp|svg|webp|tiff|tif)$/i.test(selectedFile.name) ? (
            <div className="bg-white rounded-lg p-3">
              <img
                src={`/api/files/serve-image?path=${encodeURIComponent(selectedFile.path)}`}
                alt={selectedFile.name}
                className="w-full h-auto max-h-96 object-contain rounded cursor-pointer hover:opacity-90 transition-opacity"
                onClick={() => {
                  window.open(`/api/files/serve-image?path=${encodeURIComponent(selectedFile.path)}`, '_blank')
                }}
                onError={(e) => {
                  const target = e.target as HTMLImageElement
                  target.style.display = 'none'
                  const errorDiv = document.createElement('div')
                  errorDiv.className = 'text-center text-red-400 py-8'
                  errorDiv.textContent = 'Failed to load image'
                  target.parentNode?.appendChild(errorDiv)
                }}
              />
            </div>
          ) : fileContent ? (
            /* Text File Preview */
            <div className="max-h-64 overflow-y-auto">
              <pre className="text-xs text-gray-300 bg-black/30 p-3 rounded overflow-x-auto">
                {fileContent}
              </pre>
            </div>
          ) : (
            /* File type not supported for preview */
            <div className="text-center text-gray-500 py-8">
              <File className="w-12 h-12 mx-auto mb-4 opacity-50" />
              <p>Preview not available for this file type</p>
              <p className="text-xs text-gray-600 mt-1">Use download or view buttons above</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
