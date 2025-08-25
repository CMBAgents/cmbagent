'use client';

import React, { useState, useEffect } from 'react';
import { Key, RefreshCw, AlertCircle } from 'lucide-react';

interface CredentialTest {
  provider: string;
  status: 'valid' | 'invalid' | 'error' | 'not_configured';
  message: string;
  error_details?: string;
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

interface CredentialsKeyIconProps {
  onOpenCredentialsModal: () => void;
  onStatusChange?: (status: StatusResponse | null) => void;
  refreshKey?: number;
}

export const CredentialsKeyIcon: React.FC<CredentialsKeyIconProps> = ({ 
  onOpenCredentialsModal, 
  onStatusChange,
  refreshKey = 0 
}) => {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchCredentialsStatus = async () => {
    try {
      setLoading(true);
      const response = await fetch('http://localhost:8000/api/credentials/status');
      if (response.ok) {
        const data: StatusResponse = await response.json();
        setStatus(data);
        onStatusChange?.(data);
      } else {
        console.error('Failed to fetch credentials status');
        setStatus(null);
        onStatusChange?.(null);
      }
    } catch (error) {
      console.error('Error fetching credentials status:', error);
      setStatus(null);
      onStatusChange?.(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCredentialsStatus();
    
    // Refresh every 30 seconds
    const interval = setInterval(fetchCredentialsStatus, 30000);
    
    return () => clearInterval(interval);
  }, [refreshKey]);

  const getKeyIconColor = (): string => {
    if (!status) return 'text-gray-400';
    
    const openaiValid = status.results.openai?.status === 'valid';
    const anthropicValid = status.results.anthropic?.status === 'valid';
    const vertexValid = status.results.vertex?.status === 'valid';
    
    if (!openaiValid) {
      return 'text-red-500'; // Red if OpenAI not valid
    } else if (openaiValid && anthropicValid && vertexValid) {
      return 'text-green-500'; // Green if all valid
    } else {
      return 'text-orange-500'; // Orange if OpenAI valid but others missing
    }
  };

  const getTooltipText = (): string => {
    if (!status) return 'Unable to check credential status';
    
    const openaiValid = status.results.openai?.status === 'valid';
    const anthropicValid = status.results.anthropic?.status === 'valid';
    const vertexValid = status.results.vertex?.status === 'valid';
    
    if (!openaiValid) {
      return 'OpenAI API key required - Click to configure';
    } else if (openaiValid && anthropicValid && vertexValid) {
      return 'All API credentials valid - Click to manage';
    } else {
      const missing = [];
      if (!anthropicValid) missing.push('Anthropic');
      if (!vertexValid) missing.push('Vertex AI');
      return `OpenAI valid, ${missing.join(' and ')} missing - Click to configure`;
    }
  };

  return (
    <div className="relative group">
      <button
        onClick={onOpenCredentialsModal}
        disabled={loading}
        className={`p-2 rounded transition-all duration-200 ${getKeyIconColor()} hover:bg-white/10 disabled:opacity-50`}
        title={getTooltipText()}
      >
        {loading ? (
          <RefreshCw className="w-4 h-4 animate-spin" />
        ) : (
          <Key className="w-4 h-4" />
        )}
      </button>
      
      {/* Tooltip - positioned below the icon with higher contrast */}
      <div className="absolute top-full left-1/2 transform -translate-x-1/2 mt-2 px-3 py-2 text-xs text-white bg-black border border-gray-600 rounded-lg shadow-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none z-[9999] whitespace-nowrap max-w-xs">
        {getTooltipText()}
        <div className="absolute bottom-full left-1/2 transform -translate-x-1/2 border-4 border-transparent border-b-black"></div>
      </div>
    </div>
  );
};