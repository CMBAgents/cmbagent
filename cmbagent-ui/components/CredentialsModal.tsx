'use client';

import React, { useState } from 'react';
import { X, Eye, EyeOff, Upload, Key, TestTube2, CheckCircle, XCircle, AlertTriangle, Loader2 } from 'lucide-react';

interface CredentialsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCredentialsUpdated: () => void;
}

interface CredentialTest {
  provider: string;
  status: 'valid' | 'invalid' | 'error' | 'not_configured';
  message: string;
  error_details?: string;
}

interface TestResult {
  status: string;
  results: Record<string, CredentialTest>;
  timestamp: number;
}

export const CredentialsModal: React.FC<CredentialsModalProps> = ({
  isOpen,
  onClose,
  onCredentialsUpdated
}) => {
  const [formData, setFormData] = useState({
    openai_key: '',
    anthropic_key: '',
    vertex_json: ''
  });
  
  const [showKeys, setShowKeys] = useState({
    openai: false,
    anthropic: false
  });
  
  const [testResults, setTestResults] = useState<Record<string, CredentialTest> | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  if (!isOpen) return null;

  const handleInputChange = (field: string, value: string) => {
    setFormData(prev => ({
      ...prev,
      [field]: value
    }));
    
    // Clear test results when input changes
    setTestResults(null);
  };

  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = (e) => {
        const content = e.target?.result as string;
        handleInputChange('vertex_json', content);
      };
      reader.readAsText(file);
    }
  };

  const testCredentials = async () => {
    setTesting(true);
    setTestResults(null);
    
    try {
      const response = await fetch('http://localhost:8000/api/credentials/test', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(formData),
      });

      if (response.ok) {
        const data: TestResult = await response.json();
        setTestResults(data.results);
      } else {
        console.error('Failed to test credentials');
      }
    } catch (error) {
      console.error('Error testing credentials:', error);
    } finally {
      setTesting(false);
    }
  };

  const saveCredentials = async () => {
    setSaving(true);
    
    try {
      const response = await fetch('http://localhost:8000/api/credentials/store', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(formData),
      });

      if (response.ok) {
        const data = await response.json();
        setTestResults(data.test_results);
        onCredentialsUpdated();
        
        // Auto-close after successful save
        setTimeout(() => {
          onClose();
        }, 1500);
      } else {
        console.error('Failed to save credentials');
      }
    } catch (error) {
      console.error('Error saving credentials:', error);
    } finally {
      setSaving(false);
    }
  };

  const getStatusIcon = (status: CredentialTest['status']) => {
    switch (status) {
      case 'valid':
        return <CheckCircle className="w-4 h-4 text-green-500" />;
      case 'invalid':
        return <XCircle className="w-4 h-4 text-red-500" />;
      case 'error':
        return <AlertTriangle className="w-4 h-4 text-yellow-500" />;
      default:
        return null;
    }
  };

  const hasCredentialsToTest = formData.openai_key || formData.anthropic_key || formData.vertex_json;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-6 border-b">
          <div className="flex items-center gap-2">
            <Key className="w-5 h-5 text-blue-600" />
            <h2 className="text-xl font-semibold">Manage API Credentials</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 hover:bg-gray-100 rounded transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 space-y-6">
          <div className="text-sm text-gray-600">
            Configure your API credentials for CMBAgent. These are stored in memory only and are not persisted.
          </div>

          {/* OpenAI */}
          <div className="space-y-2">
            <label className="block text-sm font-medium text-gray-700">
              OpenAI API Key
            </label>
            <div className="relative">
              <input
                type={showKeys.openai ? 'text' : 'password'}
                value={formData.openai_key}
                onChange={(e) => handleInputChange('openai_key', e.target.value)}
                placeholder="sk-..."
                className="w-full px-3 py-2 pr-10 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
              <button
                type="button"
                onClick={() => setShowKeys(prev => ({ ...prev, openai: !prev.openai }))}
                className="absolute right-2 top-2 p-1 text-gray-500 hover:text-gray-700"
              >
                {showKeys.openai ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            {testResults?.openai && (
              <div className="flex items-center gap-2 text-sm">
                {getStatusIcon(testResults.openai.status)}
                <span className={
                  testResults.openai.status === 'valid' ? 'text-green-600' :
                  testResults.openai.status === 'invalid' ? 'text-red-600' :
                  'text-yellow-600'
                }>
                  {testResults.openai.message}
                </span>
              </div>
            )}
          </div>

          {/* Anthropic */}
          <div className="space-y-2">
            <label className="block text-sm font-medium text-gray-700">
              Anthropic API Key
            </label>
            <div className="relative">
              <input
                type={showKeys.anthropic ? 'text' : 'password'}
                value={formData.anthropic_key}
                onChange={(e) => handleInputChange('anthropic_key', e.target.value)}
                placeholder="sk-ant-..."
                className="w-full px-3 py-2 pr-10 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
              <button
                type="button"
                onClick={() => setShowKeys(prev => ({ ...prev, anthropic: !prev.anthropic }))}
                className="absolute right-2 top-2 p-1 text-gray-500 hover:text-gray-700"
              >
                {showKeys.anthropic ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            {testResults?.anthropic && (
              <div className="flex items-center gap-2 text-sm">
                {getStatusIcon(testResults.anthropic.status)}
                <span className={
                  testResults.anthropic.status === 'valid' ? 'text-green-600' :
                  testResults.anthropic.status === 'invalid' ? 'text-red-600' :
                  'text-yellow-600'
                }>
                  {testResults.anthropic.message}
                </span>
              </div>
            )}
          </div>

          {/* Vertex AI */}
          <div className="space-y-2">
            <label className="block text-sm font-medium text-gray-700">
              Google Vertex AI Service Account JSON
            </label>
            <div className="space-y-2">
              <textarea
                value={formData.vertex_json}
                onChange={(e) => handleInputChange('vertex_json', e.target.value)}
                placeholder='{\n  "type": "service_account",\n  "project_id": "your-project",\n  ...\n}'
                rows={4}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm font-mono"
              />
              
              <div className="flex items-center gap-2">
                <input
                  type="file"
                  accept=".json"
                  onChange={handleFileUpload}
                  className="hidden"
                  id="vertex-file-upload"
                />
                <label
                  htmlFor="vertex-file-upload"
                  className="inline-flex items-center gap-2 px-3 py-2 text-sm border border-gray-300 rounded-md hover:bg-gray-50 cursor-pointer transition-colors"
                >
                  <Upload className="w-4 h-4" />
                  Upload JSON file
                </label>
                <span className="text-xs text-gray-500">Or paste JSON directly above</span>
              </div>
            </div>
            {testResults?.vertex && (
              <div className="flex items-center gap-2 text-sm">
                {getStatusIcon(testResults.vertex.status)}
                <span className={
                  testResults.vertex.status === 'valid' ? 'text-green-600' :
                  testResults.vertex.status === 'invalid' ? 'text-red-600' :
                  'text-yellow-600'
                }>
                  {testResults.vertex.message}
                </span>
                {testResults.vertex.error_details && (
                  <details className="text-xs text-gray-500">
                    <summary>Details</summary>
                    <pre className="mt-1 text-xs bg-gray-50 p-2 rounded overflow-x-auto">
                      {testResults.vertex.error_details}
                    </pre>
                  </details>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center justify-end gap-3 px-6 py-4 bg-gray-50 rounded-b-lg">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition-colors"
          >
            Cancel
          </button>
          
          <button
            onClick={testCredentials}
            disabled={testing || !hasCredentialsToTest}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {testing ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <TestTube2 className="w-4 h-4" />
            )}
            Test
          </button>
          
          <button
            onClick={saveCredentials}
            disabled={saving || !hasCredentialsToTest}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {saving ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Key className="w-4 h-4" />
            )}
            Save & Test
          </button>
        </div>
      </div>
    </div>
  );
};