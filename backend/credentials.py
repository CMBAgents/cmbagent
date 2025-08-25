import os
import json
import tempfile
from typing import Dict, Optional, Any
from pydantic import BaseModel
import asyncio
import aiohttp


class CredentialTest(BaseModel):
    """Model for credential test results"""
    provider: str
    status: str  # "valid", "invalid", "error", "not_configured"
    message: str
    error_details: Optional[str] = None


class CredentialStorage(BaseModel):
    """Model for storing credentials"""
    openai_key: Optional[str] = None
    anthropic_key: Optional[str] = None
    vertex_json: Optional[str] = None  # JSON string for Vertex AI


async def test_openai_credentials(api_key: str) -> CredentialTest:
    """Test OpenAI API credentials"""
    if not api_key or not api_key.startswith('sk-'):
        return CredentialTest(
            provider="openai",
            status="invalid",
            message="Invalid API key format"
        )
    
    try:
        # Test with a simple API call
        url = "https://api.openai.com/v1/models"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return CredentialTest(
                        provider="openai",
                        status="valid",
                        message="OpenAI credentials are valid"
                    )
                elif response.status == 401:
                    return CredentialTest(
                        provider="openai",
                        status="invalid",
                        message="Invalid OpenAI API key"
                    )
                else:
                    error_text = await response.text()
                    return CredentialTest(
                        provider="openai",
                        status="error",
                        message="Error testing OpenAI credentials",
                        error_details=f"HTTP {response.status}: {error_text}"
                    )
                    
    except Exception as e:
        return CredentialTest(
            provider="openai",
            status="error",
            message="Error connecting to OpenAI API",
            error_details=str(e)
        )


async def test_anthropic_credentials(api_key: str) -> CredentialTest:
    """Test Anthropic API credentials"""
    if not api_key or not api_key.startswith('sk-ant-'):
        return CredentialTest(
            provider="anthropic",
            status="invalid",
            message="Invalid API key format"
        )
    
    try:
        # Test with a simple API call to get available models or make a minimal request
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
        
        # Make a minimal test request
        data = {
            "model": "claude-3-haiku-20240307",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "Hi"}]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status == 200:
                    return CredentialTest(
                        provider="anthropic",
                        status="valid",
                        message="Anthropic credentials are valid"
                    )
                elif response.status == 401:
                    return CredentialTest(
                        provider="anthropic",
                        status="invalid",
                        message="Invalid Anthropic API key"
                    )
                else:
                    error_text = await response.text()
                    return CredentialTest(
                        provider="anthropic",
                        status="error",
                        message="Error testing Anthropic credentials",
                        error_details=f"HTTP {response.status}: {error_text}"
                    )
                    
    except Exception as e:
        return CredentialTest(
            provider="anthropic",
            status="error",
            message="Error connecting to Anthropic API",
            error_details=str(e)
        )


async def test_vertex_credentials(service_account_json: str) -> CredentialTest:
    """Test Google Vertex AI credentials"""
    try:
        # Parse the JSON to validate format
        try:
            credentials_data = json.loads(service_account_json)
            required_fields = ['type', 'project_id', 'private_key', 'client_email']
            
            for field in required_fields:
                if field not in credentials_data:
                    return CredentialTest(
                        provider="vertex",
                        status="invalid",
                        message=f"Missing required field: {field}"
                    )
                    
            if credentials_data.get('type') != 'service_account':
                return CredentialTest(
                    provider="vertex",
                    status="invalid",
                    message="Invalid service account type"
                )
                
        except json.JSONDecodeError as e:
            return CredentialTest(
                provider="vertex",
                status="invalid",
                message="Invalid JSON format",
                error_details=str(e)
            )
        
        # Try to use the credentials to make a simple API call
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request
            import google.auth
            
            # Create credentials from the JSON
            credentials = service_account.Credentials.from_service_account_info(
                credentials_data,
                scopes=['https://www.googleapis.com/auth/cloud-platform']
            )
            
            # Test token refresh
            request = Request()
            credentials.refresh(request)
            
            if credentials.token:
                return CredentialTest(
                    provider="vertex",
                    status="valid",
                    message="Vertex AI credentials are valid",
                )
            else:
                return CredentialTest(
                    provider="vertex",
                    status="error",
                    message="Unable to obtain access token"
                )
                
        except ImportError:
            return CredentialTest(
                provider="vertex",
                status="error",
                message="Google Cloud libraries not installed",
                error_details="Run: pip install google-cloud-aiplatform"
            )
        except Exception as e:
            return CredentialTest(
                provider="vertex",
                status="error",
                message="Error validating Vertex AI credentials",
                error_details=str(e)
            )
            
    except Exception as e:
        return CredentialTest(
            provider="vertex",
            status="error",
            message="Unexpected error testing Vertex AI credentials",
            error_details=str(e)
        )


async def test_all_credentials() -> Dict[str, CredentialTest]:
    """Test all available credentials from environment variables"""
    results = {}
    
    # Test OpenAI
    openai_key = os.getenv('OPENAI_API_KEY')
    if openai_key:
        results['openai'] = await test_openai_credentials(openai_key)
    else:
        results['openai'] = CredentialTest(
            provider="openai",
            status="not_configured",
            message="OPENAI_API_KEY not set in environment"
        )
    
    # Test Anthropic
    anthropic_key = os.getenv('ANTHROPIC_API_KEY')
    if anthropic_key:
        results['anthropic'] = await test_anthropic_credentials(anthropic_key)
    else:
        results['anthropic'] = CredentialTest(
            provider="anthropic",
            status="not_configured",
            message="ANTHROPIC_API_KEY not set in environment"
        )
    
    # Test Vertex AI
    vertex_creds = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    if vertex_creds and os.path.exists(vertex_creds):
        try:
            with open(vertex_creds, 'r') as f:
                vertex_json = f.read()
            results['vertex'] = await test_vertex_credentials(vertex_json)
        except Exception as e:
            results['vertex'] = CredentialTest(
                provider="vertex",
                status="error",
                message="Error reading Vertex AI credentials file",
                error_details=str(e)
            )
    else:
        results['vertex'] = CredentialTest(
            provider="vertex",
            status="not_configured",
            message="GOOGLE_APPLICATION_CREDENTIALS not set or file not found"
        )
    
    return results


def store_credentials_in_env(credentials: CredentialStorage) -> Dict[str, str]:
    """Store credentials in environment variables (session only)"""
    updates = {}
    
    if credentials.openai_key:
        os.environ['OPENAI_API_KEY'] = credentials.openai_key
        updates['OPENAI_API_KEY'] = 'Updated'
    
    if credentials.anthropic_key:
        os.environ['ANTHROPIC_API_KEY'] = credentials.anthropic_key
        updates['ANTHROPIC_API_KEY'] = 'Updated'
    
    if credentials.vertex_json:
        # Write to temporary file and set environment variable
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                f.write(credentials.vertex_json)
                temp_path = f.name
            
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = temp_path
            updates['GOOGLE_APPLICATION_CREDENTIALS'] = f'Updated (temp file: {temp_path})'
        except Exception as e:
            updates['GOOGLE_APPLICATION_CREDENTIALS'] = f'Error: {str(e)}'
    
    return updates