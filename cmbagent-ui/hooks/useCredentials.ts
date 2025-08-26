'use client';

import { useState, useCallback } from 'react';

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

interface CredentialValidation {
  canSubmitTask: boolean;
  canUseAnthropicModels: boolean;
  canUseGeminiModels: boolean;
  openaiValid: boolean;
  anthropicValid: boolean;
  vertexValid: boolean;
  statusMessage: string;
}

export const useCredentials = () => {
  const [credentialStatus, setCredentialStatus] = useState<StatusResponse | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const handleStatusChange = useCallback((status: StatusResponse | null) => {
    setCredentialStatus(status);
  }, []);

  const refreshCredentials = useCallback(() => {
    setRefreshKey(prev => prev + 1);
  }, []);

  const getValidation = useCallback((): CredentialValidation => {
    if (!credentialStatus) {
      return {
        canSubmitTask: false,
        canUseAnthropicModels: false,
        canUseGeminiModels: false,
        openaiValid: false,
        anthropicValid: false,
        vertexValid: false,
        statusMessage: 'Unable to verify credentials'
      };
    }

    const openaiValid = credentialStatus.results.openai?.status === 'valid';
    const anthropicValid = credentialStatus.results.anthropic?.status === 'valid';
    const vertexValid = credentialStatus.results.vertex?.status === 'valid';

    let statusMessage = '';
    if (!openaiValid) {
      statusMessage = 'OpenAI API key required to submit tasks';
    } else if (openaiValid && anthropicValid && vertexValid) {
      statusMessage = 'All credentials valid';
    } else {
      const missing = [];
      if (!anthropicValid) missing.push('Anthropic');
      if (!vertexValid) missing.push('Vertex AI');
      statusMessage = `Limited functionality: ${missing.join(' and ')} not configured`;
    }

    return {
      canSubmitTask: openaiValid,
      canUseAnthropicModels: anthropicValid,
      canUseGeminiModels: vertexValid,
      openaiValid,
      anthropicValid,
      vertexValid,
      statusMessage
    };
  }, [credentialStatus]);

  // Filter available models based on credential status
  const getAvailableModels = useCallback(() => {
    const validation = getValidation();
    
    const openaiModels = [
      'gpt-4.1-2025-04-14',
      'gpt-4o',
      'gpt-4o-mini',
      'gpt-5-2025-08-07',
      'o3-mini-2025-01-31'
    ];

    const anthropicModels = [
      'claude-sonnet-4-20250514',
      'claude-3.5-sonnet-20241022',
      'claude-3-haiku-20240307'
    ];

    const geminiModels = [
      'gemini-2.5-pro',
      'gemini-1.5-pro',
      'gemini-1.5-flash'
    ];

    const availableModels = [...openaiModels];

    if (validation.canUseAnthropicModels) {
      availableModels.push(...anthropicModels);
    }

    if (validation.canUseGeminiModels) {
      availableModels.push(...geminiModels);
    }

    return {
      all: availableModels,
      openai: openaiModels,
      anthropic: validation.canUseAnthropicModels ? anthropicModels : [],
      gemini: validation.canUseGeminiModels ? geminiModels : []
    };
  }, [getValidation]);

  const isModelAvailable = useCallback((model: string): boolean => {
    const availableModels = getAvailableModels();
    return availableModels.all.includes(model);
  }, [getAvailableModels]);

  return {
    credentialStatus,
    refreshKey,
    handleStatusChange,
    refreshCredentials,
    getValidation,
    getAvailableModels,
    isModelAvailable
  };
};