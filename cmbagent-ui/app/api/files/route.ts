/**
 * API Route for File Operations
 *
 * Read, write, and list files in the work directory.
 */

import { NextRequest, NextResponse } from 'next/server';
import { FrontendCodeExecutor } from '@/lib/codeExecutor';

/**
 * GET /api/files?workDir=...&path=...
 *
 * Read a file or list files in a directory.
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  try {
    const searchParams = request.nextUrl.searchParams;
    const workDir = searchParams.get('workDir');
    const filePath = searchParams.get('path');
    const action = searchParams.get('action') || 'read';

    if (!workDir) {
      return NextResponse.json(
        { success: false, error: 'workDir is required' },
        { status: 400 }
      );
    }

    const executor = new FrontendCodeExecutor(workDir);

    if (action === 'list') {
      // List files
      const subdir = filePath || '';
      const files = await executor.listFiles(subdir);
      return NextResponse.json({
        success: true,
        files,
      });
    } else if (action === 'read') {
      // Read file
      if (!filePath) {
        return NextResponse.json(
          { success: false, error: 'path is required for read action' },
          { status: 400 }
        );
      }

      try {
        const content = await executor.readFile(filePath);
        return NextResponse.json({
          success: true,
          content,
        });
      } catch (error: any) {
        if (error.code === 'ENOENT') {
          return NextResponse.json(
            { success: false, error: 'File not found' },
            { status: 404 }
          );
        }
        throw error;
      }
    } else {
      return NextResponse.json(
        { success: false, error: `Unknown action: ${action}` },
        { status: 400 }
      );
    }
  } catch (error: any) {
    console.error('File operation error:', error);
    return NextResponse.json(
      { success: false, error: error.message },
      { status: 500 }
    );
  }
}

/**
 * POST /api/files
 *
 * Write a file to the work directory.
 */
export async function POST(request: NextRequest): Promise<NextResponse> {
  try {
    const body = await request.json();
    const { workDir, path: filePath, content } = body;

    if (!workDir) {
      return NextResponse.json(
        { success: false, error: 'workDir is required' },
        { status: 400 }
      );
    }

    if (!filePath) {
      return NextResponse.json(
        { success: false, error: 'path is required' },
        { status: 400 }
      );
    }

    if (content === undefined) {
      return NextResponse.json(
        { success: false, error: 'content is required' },
        { status: 400 }
      );
    }

    const executor = new FrontendCodeExecutor(workDir);
    await executor.writeFile(filePath, content);

    return NextResponse.json({
      success: true,
      message: `File written: ${filePath}`,
    });
  } catch (error: any) {
    console.error('File write error:', error);
    return NextResponse.json(
      { success: false, error: error.message },
      { status: 500 }
    );
  }
}
