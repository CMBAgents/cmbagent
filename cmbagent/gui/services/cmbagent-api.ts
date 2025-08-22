export interface CMBAgentRequest {
  user_input: string
  max_rounds?: number
  max_n_attempts?: number
  engineer_model?: string
  researcher_model?: string
  agent?: string
  api_keys?: Record<string, string>
  work_dir?: string
}

export interface CMBAgentResponse {
  chat_history: Array<{
    name: string
    content: string | any
  }>
  tool_responses?: any[]
  final_context?: {
    work_dir: string
  }
}

export interface PlanningControlRequest extends CMBAgentRequest {
  max_rounds_control?: number
  n_plan_reviews?: number
  max_plan_steps?: number
  plan_instructions?: string
  hardware_constraints?: string
}

class CMBAgentAPI {
  private baseURL: string

  constructor() {
    // For now, use the local Next.js API endpoint
    // Later, this will point to your CMBAgent Python backend
    this.baseURL = process.env.NEXT_PUBLIC_API_BASE_URL || ''
  }

  async oneShot(request: CMBAgentRequest): Promise<CMBAgentResponse> {
    try {
      // Use local Next.js API endpoint for testing
      const response = await fetch('/api/one_shot', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(request),
      })

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error calling one_shot API:', error)
      throw error
    }
  }

  async planningAndControl(request: PlanningControlRequest): Promise<CMBAgentResponse> {
    try {
      const response = await fetch('/api/planning_and_control', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(request),
      })

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error calling planning_and_control API:', error)
      throw error
    }
  }

  async ideaGeneration(request: PlanningControlRequest): Promise<CMBAgentResponse> {
    try {
      const response = await fetch('/api/idea_generation', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(request),
      })

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      return await response.json()
    } catch (error) {
      console.error('Error calling idea_generation API:', error)
      throw error
    }
  }

  // For development/testing - simulate the actual CMBAgent response
  async simulateOneShot(request: CMBAgentRequest): Promise<CMBAgentResponse> {
    // Simulate processing time
    await new Promise(resolve => setTimeout(resolve, 2000))

    const agentName = request.agent || 'engineer'
    const model = request.engineer_model || 'gpt-4o'

    return {
      chat_history: [
        {
          name: `${agentName}_response_formatter`,
          content: `I've processed your request: "${request.user_input}" using the ${agentName} agent with ${model} model.

This is a simulated response that mimics what the actual CMBAgent would return. In the real implementation, this would contain:

1. The agent's analysis and reasoning
2. Generated code (if applicable)
3. Results and visualizations
4. Any error messages or debugging information

The actual response would come from calling the CMBAgent one_shot function with your parameters:
- Max rounds: ${request.max_rounds || 25}
- Max attempts: ${request.max_n_attempts || 6}
- Engineer model: ${request.engineer_model || 'gpt-4o'}
- Researcher model: ${request.researcher_model || 'gpt-4o'}
- Selected agent: ${request.agent || 'engineer'}
- Work directory: ${request.work_dir || 'project_dir'}

To get real responses, you'll need to:
1. Set up the CMBAgent backend
2. Update the API endpoints in next.config.js
3. Replace the simulateOneShot calls with actual API calls`
        }
      ],
      tool_responses: [],
      final_context: {
        work_dir: request.work_dir || 'project_dir'
      }
    }
  }
}

export const cmbAgentAPI = new CMBAgentAPI()
