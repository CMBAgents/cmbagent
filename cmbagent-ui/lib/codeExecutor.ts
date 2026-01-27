/**
 * Frontend Code Executor for CMBAgent
 *
 * Executes Python and Bash code locally on the user's machine
 * in an isolated virtual environment.
 */

import { exec, spawn, ChildProcess } from 'child_process';
import { promisify } from 'util';
import * as fs from 'fs/promises';
import * as path from 'path';
import * as crypto from 'crypto';
import * as os from 'os';

const execAsync = promisify(exec);

export interface CodeBlock {
  code: string;
  language: string;
}

export interface FileInfo {
  path: string;
  size: number;
  mime: string;
  checksum?: string;
}

export interface ExecutionResult {
  exitCode: number;
  output: string;
  codeFile: string;
  filesCreated: FileInfo[];
}

export interface ExecutorOptions {
  onOutput?: (data: string) => void;
  onError?: (data: string) => void;
}

/**
 * Manages Python virtual environment and code execution on the frontend.
 */
export class FrontendCodeExecutor {
  private workDir: string;
  private venvPath: string;
  private isWindows: boolean;

  constructor(workDir: string) {
    // Expand ~ to home directory
    this.workDir = workDir.replace(/^~/, os.homedir());
    this.venvPath = path.join(this.workDir, '.venv');
    this.isWindows = process.platform === 'win32';
  }

  /**
   * Initialize the executor by creating work directory and venv.
   */
  async initialize(): Promise<void> {
    // Create directory structure
    await fs.mkdir(this.workDir, { recursive: true });
    await fs.mkdir(path.join(this.workDir, 'codebase'), { recursive: true });
    await fs.mkdir(path.join(this.workDir, 'data'), { recursive: true });
    await fs.mkdir(path.join(this.workDir, 'chats'), { recursive: true });

    // Create virtual environment if it doesn't exist
    if (!(await this.exists(this.venvPath))) {
      console.log('Creating Python virtual environment...');
      await execAsync(`python3 -m venv "${this.venvPath}"`);
      console.log('Virtual environment created');

      // Upgrade pip and install common packages
      const pip = this.getPipPath();
      try {
        await execAsync(`"${pip}" install --upgrade pip`);
        await execAsync(`"${pip}" install numpy matplotlib pandas scipy`);
        console.log('Base packages installed');
      } catch (error) {
        console.warn('Failed to install some packages:', error);
      }
    }
  }

  /**
   * Check if a path exists.
   */
  private async exists(p: string): Promise<boolean> {
    try {
      await fs.access(p);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Get path to Python executable in venv.
   */
  private getPythonPath(): string {
    if (this.isWindows) {
      return path.join(this.venvPath, 'Scripts', 'python.exe');
    }
    return path.join(this.venvPath, 'bin', 'python');
  }

  /**
   * Get path to pip executable in venv.
   */
  private getPipPath(): string {
    if (this.isWindows) {
      return path.join(this.venvPath, 'Scripts', 'pip.exe');
    }
    return path.join(this.venvPath, 'bin', 'pip');
  }

  /**
   * Extract filename from code content (matches AG2's _get_file_name_from_content).
   * Looks for patterns like:
   *   # filename:step_1.py       (Python/shell comment)
   *   // filename:step_1.py      (C++ style comment)
   *   C-style and HTML comments are also supported
   */
  private extractFilenameFromCode(code: string): string | null {
    const firstLine = code.split('\n')[0].trim();

    // Patterns matching AG2's filename extraction
    const patterns = [
      /^<!--\s*(filename:)?(.+?)\s*-->$/,      // HTML comment
      /^\/\*\s*(filename:)?(.+?)\s*\*\/$/,     // C-style comment
      /^\/\/\s*(filename:)?(.+?)$/,            // C++ style comment
      /^#\s*(filename:)?(.+?)$/,               // Python/shell comment
    ];

    for (const pattern of patterns) {
      const match = firstLine.match(pattern);
      if (match) {
        let filename = match[2].trim();
        // Only process if it looks like a valid filename
        if (filename && !filename.includes(' ') && filename.includes('.')) {
          // Strip 'codebase/' prefix if present - files are already saved to codebase folder
          if (filename.startsWith('codebase/')) {
            filename = filename.substring('codebase/'.length);
          }
          // Get just the basename (strip any remaining path components)
          const parts = filename.split('/');
          return parts[parts.length - 1];
        }
      }
    }
    return null;
  }

  /**
   * Execute code blocks and return the result.
   */
  async executeCodeBlocks(
    codeBlocks: CodeBlock[],
    timeout: number = 86400, // 24 hours default
    options: ExecutorOptions = {}
  ): Promise<ExecutionResult> {
    // Snapshot files before execution
    const beforeFiles = await this.scanWorkDir();

    let allOutput = '';
    let exitCode = 0;
    let codeFile = '';

    for (const block of codeBlocks) {
      const { code, language } = block;
      const lang = language.toLowerCase();

      // Try to extract filename from code content (e.g., "# filename:step_1.py")
      let filename = this.extractFilenameFromCode(code);

      // Fall back to hash-based filename if not found in code
      if (!filename) {
        const hash = crypto.createHash('md5').update(code).digest('hex').slice(0, 8);
        const timestamp = Date.now();
        const ext = this.getExtension(lang);
        filename = `code_${timestamp}_${hash}.${ext}`;
      }

      codeFile = path.join(this.workDir, 'codebase', filename);

      // Write code to file
      await fs.writeFile(codeFile, code, 'utf-8');

      // Build and execute command
      let command: string;
      let args: string[];

      if (lang === 'python' || lang === 'py') {
        command = this.getPythonPath();
        args = [codeFile];
      } else if (lang === 'bash' || lang === 'sh' || lang === 'shell') {
        command = 'bash';
        args = [codeFile];
      } else {
        allOutput += `Unsupported language: ${language}\n`;
        exitCode = 1;
        break;
      }

      try {
        const result = await this.runCommand(command, args, timeout, options);
        allOutput += result.output;
        exitCode = result.exitCode;

        if (exitCode !== 0) {
          break; // Stop on first error
        }
      } catch (error: any) {
        if (error.killed || error.signal === 'SIGTERM') {
          allOutput += `\nExecution timed out after ${timeout} seconds`;
          exitCode = 124;
        } else {
          allOutput += `\nExecution error: ${error.message}`;
          exitCode = error.code || 1;
        }
        break;
      }
    }

    // Detect new/modified files
    const afterFiles = await this.scanWorkDir();
    const filesCreated = await this.diffFiles(beforeFiles, afterFiles);

    return {
      exitCode,
      output: allOutput,
      codeFile,
      filesCreated,
    };
  }

  /**
   * Get path to the venv bin directory.
   */
  private getVenvBinPath(): string {
    if (this.isWindows) {
      return path.join(this.venvPath, 'Scripts');
    }
    return path.join(this.venvPath, 'bin');
  }

  /**
   * Run a command with timeout and streaming output.
   */
  private runCommand(
    command: string,
    args: string[],
    timeout: number,
    options: ExecutorOptions
  ): Promise<{ exitCode: number; output: string }> {
    return new Promise((resolve, reject) => {
      let output = '';
      let resolved = false;

      // Add venv bin to PATH so bash scripts can find python
      const venvBin = this.getVenvBinPath();
      const currentPath = process.env.PATH || '';
      const newPath = `${venvBin}${path.delimiter}${currentPath}`;

      const proc: ChildProcess = spawn(command, args, {
        cwd: this.workDir,
        env: {
          ...process.env,
          PATH: newPath,
          PYTHONUNBUFFERED: '1',
          MPLBACKEND: 'Agg', // Non-interactive matplotlib backend
          VIRTUAL_ENV: this.venvPath, // Set VIRTUAL_ENV for proper venv detection
        },
        stdio: ['pipe', 'pipe', 'pipe'],
      });

      // Set timeout
      const timeoutId = setTimeout(() => {
        if (!resolved) {
          proc.kill('SIGTERM');
          setTimeout(() => proc.kill('SIGKILL'), 5000); // Force kill after 5s
        }
      }, timeout * 1000);

      // Collect stdout
      proc.stdout?.on('data', (data: Buffer) => {
        const text = data.toString();
        output += text;
        options.onOutput?.(text);
      });

      // Collect stderr
      proc.stderr?.on('data', (data: Buffer) => {
        const text = data.toString();
        output += text;
        options.onError?.(text);
      });

      // Handle completion
      proc.on('close', (code: number | null, signal: string | null) => {
        clearTimeout(timeoutId);
        resolved = true;

        if (signal === 'SIGTERM' || signal === 'SIGKILL') {
          reject({ killed: true, signal, message: 'Process timed out' });
        } else {
          resolve({
            exitCode: code ?? 0,
            output,
          });
        }
      });

      proc.on('error', (error: Error) => {
        clearTimeout(timeoutId);
        resolved = true;
        reject(error);
      });
    });
  }

  /**
   * Get file extension for a language.
   */
  private getExtension(lang: string): string {
    const extensions: Record<string, string> = {
      python: 'py',
      py: 'py',
      bash: 'sh',
      sh: 'sh',
      shell: 'sh',
    };
    return extensions[lang] || 'txt';
  }

  /**
   * Scan work directory for all files.
   */
  private async scanWorkDir(): Promise<Map<string, { mtime: number; size: number }>> {
    const files = new Map<string, { mtime: number; size: number }>();
    await this.scanDir(this.workDir, files);
    return files;
  }

  /**
   * Recursively scan a directory.
   */
  private async scanDir(
    dir: string,
    files: Map<string, { mtime: number; size: number }>
  ): Promise<void> {
    try {
      const entries = await fs.readdir(dir, { withFileTypes: true });

      for (const entry of entries) {
        const fullPath = path.join(dir, entry.name);

        // Skip certain directories
        if (entry.isDirectory()) {
          if (['.venv', 'node_modules', '__pycache__', '.git'].includes(entry.name)) {
            continue;
          }
          await this.scanDir(fullPath, files);
        } else if (entry.isFile()) {
          try {
            const stat = await fs.stat(fullPath);
            files.set(fullPath, {
              mtime: stat.mtimeMs,
              size: stat.size,
            });
          } catch {
            // Skip files we can't stat
          }
        }
      }
    } catch {
      // Skip directories we can't read
    }
  }

  /**
   * Find new or modified files.
   */
  private async diffFiles(
    before: Map<string, { mtime: number; size: number }>,
    after: Map<string, { mtime: number; size: number }>
  ): Promise<FileInfo[]> {
    const newFiles: FileInfo[] = [];

    // Use Array.from for compatibility
    const afterEntries = Array.from(after.entries());

    for (const [filePath, info] of afterEntries) {
      const beforeInfo = before.get(filePath);

      // File is new or modified
      if (!beforeInfo || beforeInfo.mtime < info.mtime) {
        const relativePath = path.relative(this.workDir, filePath);
        const ext = path.extname(filePath).toLowerCase();

        // Calculate checksum for binary files
        let checksum: string | undefined;
        if (this.isBinaryExtension(ext)) {
          try {
            const content = await fs.readFile(filePath);
            checksum = crypto.createHash('md5').update(content).digest('hex');
          } catch {
            // Skip checksum on error
          }
        }

        newFiles.push({
          path: relativePath,
          size: info.size,
          mime: this.getMimeType(ext),
          checksum,
        });
      }
    }

    return newFiles;
  }

  /**
   * Check if extension is binary.
   */
  private isBinaryExtension(ext: string): boolean {
    return ['.png', '.jpg', '.jpeg', '.gif', '.pdf', '.npy', '.fits', '.pkl'].includes(ext);
  }

  /**
   * Get MIME type for file extension.
   */
  private getMimeType(ext: string): string {
    const mimes: Record<string, string> = {
      '.png': 'image/png',
      '.jpg': 'image/jpeg',
      '.jpeg': 'image/jpeg',
      '.gif': 'image/gif',
      '.svg': 'image/svg+xml',
      '.pdf': 'application/pdf',
      '.csv': 'text/csv',
      '.json': 'application/json',
      '.txt': 'text/plain',
      '.py': 'text/x-python',
      '.sh': 'text/x-shellscript',
      '.md': 'text/markdown',
      '.html': 'text/html',
      '.npy': 'application/octet-stream',
      '.fits': 'application/fits',
      '.pkl': 'application/octet-stream',
    };
    return mimes[ext] || 'application/octet-stream';
  }

  /**
   * Install a Python package.
   */
  async installPackage(packageName: string): Promise<{ success: boolean; output: string }> {
    try {
      const pip = this.getPipPath();
      const { stdout, stderr } = await execAsync(`"${pip}" install "${packageName}"`, {
        cwd: this.workDir,
        timeout: 300000, // 5 minute timeout for installs
      });
      return { success: true, output: stdout + stderr };
    } catch (error: any) {
      return { success: false, output: error.message };
    }
  }

  /**
   * Install multiple packages.
   */
  async installPackages(packages: string[]): Promise<{ success: boolean; output: string; failed: string[] }> {
    const failed: string[] = [];
    let output = '';

    for (const pkg of packages) {
      const result = await this.installPackage(pkg);
      output += result.output + '\n';
      if (!result.success) {
        failed.push(pkg);
      }
    }

    return {
      success: failed.length === 0,
      output,
      failed,
    };
  }

  /**
   * Write a file to the work directory.
   */
  async writeFile(relativePath: string, content: string): Promise<void> {
    const fullPath = path.join(this.workDir, relativePath);
    const dir = path.dirname(fullPath);
    await fs.mkdir(dir, { recursive: true });
    await fs.writeFile(fullPath, content, 'utf-8');
  }

  /**
   * Read a file from the work directory.
   */
  async readFile(relativePath: string): Promise<string> {
    const fullPath = path.join(this.workDir, relativePath);
    return await fs.readFile(fullPath, 'utf-8');
  }

  /**
   * List files in the work directory.
   */
  async listFiles(subdir: string = ''): Promise<string[]> {
    const dir = path.join(this.workDir, subdir);
    const files: string[] = [];

    const scan = async (d: string, prefix: string) => {
      try {
        const entries = await fs.readdir(d, { withFileTypes: true });
        for (const entry of entries) {
          if (entry.isFile()) {
            files.push(path.join(prefix, entry.name));
          } else if (entry.isDirectory() && !entry.name.startsWith('.')) {
            await scan(path.join(d, entry.name), path.join(prefix, entry.name));
          }
        }
      } catch {
        // Ignore errors
      }
    };

    await scan(dir, subdir);
    return files;
  }

  /**
   * Get the work directory path.
   */
  getWorkDir(): string {
    return this.workDir;
  }

  /**
   * Clean up old files (optional maintenance).
   */
  async cleanup(maxAgeDays: number = 7): Promise<number> {
    const maxAgeMs = maxAgeDays * 24 * 60 * 60 * 1000;
    const now = Date.now();
    let deleted = 0;

    const files = await this.scanWorkDir();
    const fileEntries = Array.from(files.entries());

    for (const [filePath, info] of fileEntries) {
      if (now - info.mtime > maxAgeMs) {
        try {
          await fs.unlink(filePath);
          deleted++;
        } catch {
          // Ignore deletion errors
        }
      }
    }

    return deleted;
  }
}
