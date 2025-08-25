'use client';

import React from 'react';
import { useCredentials } from '../hooks/useCredentials';

interface ModelSelectorProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
  includeModelTypes?: ('openai' | 'anthropic' | 'gemini')[];
}

const MODEL_INFO = {
  // OpenAI Models
  'gpt-4.1-2025-04-14': { name: 'GPT-4.1', provider: 'openai' as const },
  'gpt-4o': { name: 'GPT-4o', provider: 'openai' as const },
  'gpt-4o-mini': { name: 'GPT-4o Mini', provider: 'openai' as const },
  'gpt-5-2025-08-07': { name: 'GPT-5', provider: 'openai' as const },
  'o3-mini-2025-01-31': { name: 'o3-mini', provider: 'openai' as const },
  
  // Anthropic Models
  'claude-sonnet-4-20250514': { name: 'Claude Sonnet 4', provider: 'anthropic' as const },
  'claude-3.5-sonnet-20241022': { name: 'Claude 3.5 Sonnet', provider: 'anthropic' as const },
  'claude-3-haiku-20240307': { name: 'Claude 3 Haiku', provider: 'anthropic' as const },
  
  // Gemini Models
  'gemini-2.5-pro': { name: 'Gemini 2.5 Pro', provider: 'gemini' as const },
  'gemini-1.5-pro': { name: 'Gemini 1.5 Pro', provider: 'gemini' as const },
  'gemini-1.5-flash': { name: 'Gemini 1.5 Flash', provider: 'gemini' as const },
};

export const ModelSelector: React.FC<ModelSelectorProps> = ({
  value,
  onChange,
  disabled = false,
  className = "",
  includeModelTypes = ['openai', 'anthropic', 'gemini']
}) => {
  const { getAvailableModels, getValidation } = useCredentials();
  
  const availableModels = getAvailableModels();
  const validation = getValidation();
  
  // Filter models based on what's available and what types are requested
  const filteredModels = Object.entries(MODEL_INFO).filter(([modelKey, modelInfo]) => {
    // Check if this model type is requested
    if (!includeModelTypes.includes(modelInfo.provider)) return false;
    
    // Check if model is available based on credentials
    if (modelInfo.provider === 'openai') return validation.openaiValid;
    if (modelInfo.provider === 'anthropic') return validation.canUseAnthropicModels;
    if (modelInfo.provider === 'gemini') return validation.canUseGeminiModels;
    
    return false;
  });
  
  // Group models by provider
  const groupedModels = filteredModels.reduce((acc, [modelKey, modelInfo]) => {
    if (!acc[modelInfo.provider]) acc[modelInfo.provider] = [];
    acc[modelInfo.provider].push([modelKey, modelInfo]);
    return acc;
  }, {} as Record<string, [string, typeof MODEL_INFO[keyof typeof MODEL_INFO]][]>);
  
  const providerNames = {
    openai: 'OpenAI',
    anthropic: 'Anthropic',
    gemini: 'Google Gemini'
  };
  
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className={className}
    >
      {Object.entries(groupedModels).map(([provider, models]) => (
        <optgroup key={provider} label={providerNames[provider as keyof typeof providerNames]}>
          {models.map(([modelKey, modelInfo]) => (
            <option key={modelKey} value={modelKey}>
              {modelInfo.name}
            </option>
          ))}
        </optgroup>
      ))}
      
      {filteredModels.length === 0 && (
        <option disabled>No models available - check credentials</option>
      )}
    </select>
  );
};