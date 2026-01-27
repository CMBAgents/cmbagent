/**
 * API Route for Code Execution
 *
 * This server-side route handles code execution requests from the WebSocket handler.
 * It uses the FrontendCodeExecutor to run code in a local Python virtual environment.
 */

import { NextRequest, NextResponse } from 'next/server';
import { FrontendCodeExecutor, CodeBlock, ExecutionResult } from '@/lib/codeExecutor';

export interface ExecuteRequest {
  workDir: string;
  codeBlocks: CodeBlock[];
  timeout?: number;
}

export interface ExecuteResponse {
  success: boolean;
  result?: ExecutionResult;
  error?: string;
}

/**
 * POST /api/execute
 *
 * Execute code blocks in the local Python environment.
 */
export async function POST(request: NextRequest): Promise<NextResponse<ExecuteResponse>> {
  try {
    const body: ExecuteRequest = await request.json();
    const { workDir, codeBlocks, timeout = 86400 } = body;

    // Validate request
    if (!workDir) {
      return NextResponse.json(
        { success: false, error: 'workDir is required' },
        { status: 400 }
      );
    }

    if (!codeBlocks || !Array.isArray(codeBlocks) || codeBlocks.length === 0) {
      return NextResponse.json(
        { success: false, error: 'codeBlocks array is required' },
        { status: 400 }
      );
    }

    // Validate code blocks
    for (const block of codeBlocks) {
      if (!block.code || !block.language) {
        return NextResponse.json(
          { success: false, error: 'Each code block must have code and language' },
          { status: 400 }
        );
      }
    }

    // Create executor and initialize
    const executor = new FrontendCodeExecutor(workDir);
    await executor.initialize();

    // Execute code blocks
    const result = await executor.executeCodeBlocks(codeBlocks, timeout);

    return NextResponse.json({
      success: true,
      result,
    });
  } catch (error: any) {
    console.error('Execution error:', error);

    return NextResponse.json(
      {
        success: false,
        error: error.message || 'Unknown execution error',
      },
      { status: 500 }
    );
  }
}

/**
 * GET /api/execute
 *
 * Health check for the execution endpoint.
 */
export async function GET(): Promise<NextResponse> {
  return NextResponse.json({
    status: 'ok',
    message: 'Execution endpoint ready',
    timestamp: new Date().toISOString(),
  });
}
