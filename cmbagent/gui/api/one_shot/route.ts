import { NextRequest, NextResponse } from 'next/server'

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const { user_input, max_rounds, max_n_attempts, engineer_model, researcher_model, agent, api_keys, work_dir } = body

    console.log('Received one_shot request:', {
      user_input,
      max_rounds,
      max_n_attempts,
      engineer_model,
      researcher_model,
      agent,
      work_dir
    })

    // Simulate processing time
    await new Promise(resolve => setTimeout(resolve, 2000))

    // Simulate the CMBAgent one_shot response
    const response = {
      chat_history: [
        {
          name: `${agent}_response_formatter`,
          content: `I've processed your request: "${user_input}" using the ${agent} agent.

**Configuration Used:**
- Max rounds: ${max_rounds}
- Max attempts: ${max_n_attempts}
- Engineer model: ${engineer_model}
- Researcher model: ${researcher_model}
- Selected agent: ${agent}
- Work directory: ${work_dir}

**Response Content:**
This is a simulated response from the CMBAgent backend. In the real implementation, this would contain:

1. **Agent Analysis**: The ${agent} agent would analyze your request
2. **Code Generation**: If applicable, generate and execute code
3. **Results**: Display plots, data, or analysis results
4. **Debugging**: Any error messages or execution logs

**Example for Engineer Agent:**
If you asked to "Plot a 3D Möbius strip", the engineer agent would:
- Generate matplotlib code
- Execute it
- Return the plot image
- Provide the code for reference

**Example for Researcher Agent:**
If you asked about "black holes vs white holes", the researcher agent would:
- Provide scientific explanation
- Reference relevant theories
- Compare and contrast concepts
- Suggest further reading

**Next Steps:**
To get real responses, you'll need to:
1. Set up the actual CMBAgent Python backend
2. Update the API endpoints in next.config.js to point to your backend
3. Replace this simulated endpoint with real CMBAgent function calls

The interface is now properly connected and ready for real backend integration!`
        }
      ],
      tool_responses: [],
      final_context: {
        work_dir: work_dir || 'project_dir'
      }
    }

    return NextResponse.json(response)
  } catch (error) {
    console.error('Error in one_shot API:', error)
    return NextResponse.json(
      { 
        error: 'Internal server error',
        details: error instanceof Error ? error.message : 'Unknown error',
        chat_history: []
      },
      { status: 500 }
    )
  }
}
