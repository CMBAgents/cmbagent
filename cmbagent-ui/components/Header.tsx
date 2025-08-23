import { Brain, Github, Wifi, WifiOff } from 'lucide-react'

interface HeaderProps {
  status?: string
  isConnected?: boolean
}

export default function Header({ status = 'Ready', isConnected = false }: HeaderProps) {
  return (
    <header className="bg-black/20 backdrop-blur-sm border-b border-white/10">
      <div className="container mx-auto px-4 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-blue-600 rounded-lg">
              <Brain className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-white">CMBAgent UI</h1>
              <p className="text-sm text-gray-300">AI-powered scientific computing</p>
            </div>
          </div>
          
          <div className="flex items-center space-x-4">
            <div className="text-right">
              <div className="flex items-center space-x-2">
                {isConnected ? (
                  <Wifi className="w-4 h-4 text-green-400" />
                ) : (
                  <WifiOff className="w-4 h-4 text-gray-400" />
                )}
                <p className="text-sm text-gray-300">{status}</p>
              </div>
              <p className="text-xs text-gray-400">Engineer Agent with GPT-4o</p>
            </div>
            
            <a 
              href="https://github.com/CMBAgents/cmbagent" 
              target="_blank" 
              rel="noopener noreferrer"
              className="p-2 text-gray-400 hover:text-white transition-colors"
            >
              <Github className="w-5 h-5" />
            </a>
          </div>
        </div>
      </div>
    </header>
  )
}
