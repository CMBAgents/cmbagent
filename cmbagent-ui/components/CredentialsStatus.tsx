'use client';

import React, { useState, useEffect } from 'react';
import { Key, RefreshCw, AlertCircle, CheckCircle, XCircle, Settings } from 'lucide-react';

interface CredentialTest {
  provider: string;
  status: 'valid' | 'invalid' | 'error' | 'not_configured';
  message: string;
  error_details?: string;
}

interface CredentialsStatusProps {
  onOpenCredentialsModal: () => void;
}

interface StatusResponse {
  status: string;
  summary: {
    total: number;
    valid: number;
    invalid: number;
    not_configured: number;
    errors: number;
  };
  results: Record<string, CredentialTest>;
  timestamp: number;
}

const StatusIndicator: React.FC<{ status: CredentialTest['status']; size?: 'sm' | 'md' }> = ({ 
  status, 
  size = 'md' 
}) => {
  const sizeClasses = size === 'sm' ? 'w-3 h-3' : 'w-4 h-4';
  
  switch (status) {
    case 'valid':
      return <div className={`${sizeClasses} bg-green-500 rounded-full shadow-sm ring-2 ring-green-200`} />;
    case 'invalid':
      return <div className={`${sizeClasses} bg-red-500 rounded-full shadow-sm ring-2 ring-red-200`} />;
    case 'error':
      return <div className={`${sizeClasses} bg-yellow-500 rounded-full shadow-sm ring-2 ring-yellow-200`} />;
    case 'not_configured':
      return <div className={`${sizeClasses} bg-gray-400 rounded-full shadow-sm ring-2 ring-gray-200`} />;
    default:
      return <div className={`${sizeClasses} bg-gray-300 rounded-full shadow-sm`} />;
  }
};

const ProviderIcon: React.FC<{ provider: string }> = ({ provider }) => {
  switch (provider) {
    case 'openai':
      return (
        <div className="w-6 h-6 bg-black text-white rounded-sm flex items-center justify-center text-xs font-bold">
          AI
        </div>
      );
    case 'anthropic':
      return (
        <div className="w-6 h-6 bg-orange-500 text-white rounded-sm flex items-center justify-center text-xs font-bold">
          Cl
        </div>
      );
    case 'vertex':
      return (
        <div className="w-6 h-6 bg-blue-500 text-white rounded-sm flex items-center justify-center text-xs font-bold">
          G
        </div>
      );
    default:
      return (
        <div className="w-6 h-6 bg-gray-500 text-white rounded-sm flex items-center justify-center text-xs font-bold">
          ?
        </div>
      );
  }
};

export const CredentialsStatus: React.FC<CredentialsStatusProps> = ({ onOpenCredentialsModal }) => {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);

  const fetchCredentialsStatus = async () => {
    try {
      setLoading(true);
      const response = await fetch('http://localhost:8000/api/credentials/status');
      if (response.ok) {
        const data: StatusResponse = await response.json();
        setStatus(data);
        setLastChecked(new Date());
      } else {
        console.error('Failed to fetch credentials status');
      }
    } catch (error) {
      console.error('Error fetching credentials status:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCredentialsStatus();
    
    // Refresh every 30 seconds
    const interval = setInterval(fetchCredentialsStatus, 30000);
    
    return () => clearInterval(interval);
  }, []);

  const getOverallStatus = (): 'good' | 'warning' | 'error' | 'unknown' => {
    if (!status) return 'unknown';
    
    if (status.summary.valid > 0 && status.summary.invalid === 0 && status.summary.errors === 0) {
      return 'good';
    } else if (status.summary.valid > 0) {
      return 'warning';
    } else {
      return 'error';
    }
  };

  const overallStatus = getOverallStatus();
  const overallColor = {
    good: 'text-green-600 bg-green-50 border-green-200',
    warning: 'text-yellow-600 bg-yellow-50 border-yellow-200',
    error: 'text-red-600 bg-red-50 border-red-200',
    unknown: 'text-gray-600 bg-gray-50 border-gray-200'
  }[overallStatus];

  return (
    <div className={`border rounded-lg p-4 ${overallColor}`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Key className="w-5 h-5" />
          <h3 className="font-medium text-sm">API Credentials</h3>
          {loading && <RefreshCw className="w-4 h-4 animate-spin" />}
        </div>
        
        <div className="flex items-center gap-2">
          <button
            onClick={fetchCredentialsStatus}
            disabled={loading}
            className="p-1 hover:bg-white/50 rounded transition-colors"
            title="Refresh status"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          
          <button
            onClick={onOpenCredentialsModal}
            className="p-1 hover:bg-white/50 rounded transition-colors"
            title="Manage credentials"
          >
            <Settings className="w-4 h-4" />
          </button>
        </div>
      </div>

      {status ? (
        <div className="space-y-2">
          {Object.entries(status.results).map(([provider, result]) => (
            <div key={provider} className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <ProviderIcon provider={provider} />
                <span className="text-sm font-medium capitalize">
                  {provider === 'vertex' ? 'Vertex AI' : provider}
                </span>
              </div>
              
              <div className="flex items-center gap-2">
                <StatusIndicator status={result.status} size="sm" />
                <span className="text-xs text-gray-600 min-w-0 max-w-32 truncate" title={result.message}>
                  {result.status === 'valid' ? '✓' : 
                   result.status === 'not_configured' ? 'Not set' :
                   result.status === 'invalid' ? 'Invalid' : 'Error'}
                </span>
              </div>
            </div>
          ))}
          
          {lastChecked && (
            <div className="text-xs text-gray-500 mt-2 pt-2 border-t border-gray-200">
              Last checked: {lastChecked.toLocaleTimeString()}
            </div>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <AlertCircle className="w-4 h-4" />
          Failed to load status
        </div>
      )}
    </div>
  );
};