/**
 * API Route for Package Installation
 *
 * Installs Python packages in the work directory's virtual environment.
 */

import { NextRequest, NextResponse } from 'next/server';
import { FrontendCodeExecutor } from '@/lib/codeExecutor';

export interface InstallRequest {
  workDir: string;
  packages: string[];
}

export interface InstallResponse {
  success: boolean;
  output?: string;
  failed?: string[];
  error?: string;
}

/**
 * POST /api/install
 *
 * Install Python packages in the work directory's venv.
 */
export async function POST(request: NextRequest): Promise<NextResponse<InstallResponse>> {
  try {
    const body: InstallRequest = await request.json();
    const { workDir, packages } = body;

    // Validate request
    if (!workDir) {
      return NextResponse.json(
        { success: false, error: 'workDir is required' },
        { status: 400 }
      );
    }

    if (!packages || !Array.isArray(packages) || packages.length === 0) {
      return NextResponse.json(
        { success: false, error: 'packages array is required' },
        { status: 400 }
      );
    }

    // Validate package names (basic security check)
    const validPackagePattern = /^[a-zA-Z0-9_-]+(\[.+\])?([<>=!]+[a-zA-Z0-9._-]+)?$/;
    for (const pkg of packages) {
      if (!validPackagePattern.test(pkg)) {
        return NextResponse.json(
          { success: false, error: `Invalid package name: ${pkg}` },
          { status: 400 }
        );
      }
    }

    // Create executor and initialize
    const executor = new FrontendCodeExecutor(workDir);
    await executor.initialize();

    // Install packages
    const result = await executor.installPackages(packages);

    return NextResponse.json({
      success: result.success,
      output: result.output,
      failed: result.failed,
    });
  } catch (error: any) {
    console.error('Installation error:', error);

    return NextResponse.json(
      {
        success: false,
        error: error.message || 'Unknown installation error',
      },
      { status: 500 }
    );
  }
}
