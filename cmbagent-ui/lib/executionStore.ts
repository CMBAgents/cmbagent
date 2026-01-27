/**
 * Execution Store for CMBAgent Frontend
 *
 * Persists pending and completed executions to disk to survive
 * app restarts and network disconnections.
 */

import * as fs from 'fs/promises';
import * as path from 'path';
import * as os from 'os';

export interface CodeBlock {
  code: string;
  language: string;
}

export interface ExecutionResult {
  exitCode: number;
  output: string;
  codeFile: string;
  filesCreated: Array<{
    path: string;
    size: number;
    mime: string;
    checksum?: string;
  }>;
}

export interface PendingExecution {
  executionId: string;
  taskId: string;
  workDir: string;
  codeBlocks: CodeBlock[];
  timeout: number;
  status: 'pending' | 'running' | 'completed' | 'failed';
  startedAt: string;  // ISO timestamp
  completedAt?: string;
  result?: ExecutionResult;
  error?: string;
  backendUrl?: string;
}

export interface ExecutionQueue {
  version: number;
  executions: Record<string, PendingExecution>;
  lastUpdated: string;
}

/**
 * Manages persistent storage of execution state.
 *
 * This allows the frontend to:
 * 1. Resume executions after app restart
 * 2. Send results to backend after reconnection
 * 3. Track execution history
 */
export class ExecutionStore {
  private storePath: string;
  private queue: ExecutionQueue | null = null;
  private saveDebounceTimer: NodeJS.Timeout | null = null;

  constructor(workDir?: string) {
    // Default to ~/.cmbagent/execution_queue.json
    const baseDir = workDir
      ? workDir.replace(/^~/, os.homedir())
      : path.join(os.homedir(), '.cmbagent');
    this.storePath = path.join(baseDir, 'execution_queue.json');
  }

  /**
   * Initialize the store by loading from disk.
   */
  async initialize(): Promise<void> {
    await this.ensureDirectory();
    await this.load();
  }

  /**
   * Ensure the store directory exists.
   */
  private async ensureDirectory(): Promise<void> {
    const dir = path.dirname(this.storePath);
    await fs.mkdir(dir, { recursive: true });
  }

  /**
   * Load queue from disk.
   */
  private async load(): Promise<void> {
    try {
      const data = await fs.readFile(this.storePath, 'utf-8');
      this.queue = JSON.parse(data);

      // Migrate old versions if needed
      if (!this.queue || this.queue.version !== 1) {
        this.queue = this.createEmptyQueue();
      }
    } catch (error: any) {
      if (error.code === 'ENOENT') {
        // File doesn't exist, create empty queue
        this.queue = this.createEmptyQueue();
      } else {
        console.error('Failed to load execution queue:', error);
        this.queue = this.createEmptyQueue();
      }
    }
  }

  /**
   * Create an empty queue.
   */
  private createEmptyQueue(): ExecutionQueue {
    return {
      version: 1,
      executions: {},
      lastUpdated: new Date().toISOString(),
    };
  }

  /**
   * Save queue to disk (debounced).
   */
  private async save(): Promise<void> {
    if (!this.queue) return;

    // Debounce saves to avoid excessive disk writes
    if (this.saveDebounceTimer) {
      clearTimeout(this.saveDebounceTimer);
    }

    this.saveDebounceTimer = setTimeout(async () => {
      try {
        this.queue!.lastUpdated = new Date().toISOString();
        await fs.writeFile(
          this.storePath,
          JSON.stringify(this.queue, null, 2),
          'utf-8'
        );
      } catch (error) {
        console.error('Failed to save execution queue:', error);
      }
    }, 100);
  }

  /**
   * Force immediate save (for critical operations).
   */
  async forceSave(): Promise<void> {
    if (this.saveDebounceTimer) {
      clearTimeout(this.saveDebounceTimer);
      this.saveDebounceTimer = null;
    }

    if (!this.queue) return;

    this.queue.lastUpdated = new Date().toISOString();
    await fs.writeFile(
      this.storePath,
      JSON.stringify(this.queue, null, 2),
      'utf-8'
    );
  }

  /**
   * Add a new pending execution.
   */
  async addPending(execution: Omit<PendingExecution, 'status' | 'startedAt'>): Promise<void> {
    if (!this.queue) await this.initialize();

    this.queue!.executions[execution.executionId] = {
      ...execution,
      status: 'pending',
      startedAt: new Date().toISOString(),
    };

    await this.save();
  }

  /**
   * Mark execution as running.
   */
  async markRunning(executionId: string): Promise<void> {
    if (!this.queue) await this.initialize();

    const execution = this.queue!.executions[executionId];
    if (execution) {
      execution.status = 'running';
      await this.save();
    }
  }

  /**
   * Mark execution as completed with result.
   */
  async markCompleted(executionId: string, result: ExecutionResult): Promise<void> {
    if (!this.queue) await this.initialize();

    const execution = this.queue!.executions[executionId];
    if (execution) {
      execution.status = 'completed';
      execution.completedAt = new Date().toISOString();
      execution.result = result;
      await this.save();
    }
  }

  /**
   * Mark execution as failed with error.
   */
  async markFailed(executionId: string, error: string): Promise<void> {
    if (!this.queue) await this.initialize();

    const execution = this.queue!.executions[executionId];
    if (execution) {
      execution.status = 'failed';
      execution.completedAt = new Date().toISOString();
      execution.error = error;
      await this.save();
    }
  }

  /**
   * Get an execution by ID.
   */
  async get(executionId: string): Promise<PendingExecution | null> {
    if (!this.queue) await this.initialize();
    return this.queue!.executions[executionId] || null;
  }

  /**
   * Get all executions with a specific status.
   */
  async getByStatus(status: PendingExecution['status']): Promise<PendingExecution[]> {
    if (!this.queue) await this.initialize();
    return Object.values(this.queue!.executions).filter(e => e.status === status);
  }

  /**
   * Get all completed executions that need to be sent to backend.
   */
  async getCompletedToSend(): Promise<PendingExecution[]> {
    if (!this.queue) await this.initialize();
    return Object.values(this.queue!.executions).filter(
      e => e.status === 'completed' && e.result
    );
  }

  /**
   * Get all running executions (may need to be resumed after restart).
   */
  async getRunning(): Promise<PendingExecution[]> {
    if (!this.queue) await this.initialize();
    return Object.values(this.queue!.executions).filter(e => e.status === 'running');
  }

  /**
   * Get all executions for a specific task.
   */
  async getByTask(taskId: string): Promise<PendingExecution[]> {
    if (!this.queue) await this.initialize();
    return Object.values(this.queue!.executions).filter(e => e.taskId === taskId);
  }

  /**
   * Remove an execution after it's been successfully sent to backend.
   */
  async remove(executionId: string): Promise<void> {
    if (!this.queue) await this.initialize();
    delete this.queue!.executions[executionId];
    await this.save();
  }

  /**
   * Remove all executions for a task.
   */
  async removeByTask(taskId: string): Promise<void> {
    if (!this.queue) await this.initialize();

    for (const [id, execution] of Object.entries(this.queue!.executions)) {
      if (execution.taskId === taskId) {
        delete this.queue!.executions[id];
      }
    }

    await this.save();
  }

  /**
   * Clean up old completed/failed executions.
   */
  async cleanup(maxAgeDays: number = 7): Promise<number> {
    if (!this.queue) await this.initialize();

    const maxAgeMs = maxAgeDays * 24 * 60 * 60 * 1000;
    const now = Date.now();
    let removed = 0;

    for (const [id, execution] of Object.entries(this.queue!.executions)) {
      const completedAt = execution.completedAt
        ? new Date(execution.completedAt).getTime()
        : null;

      // Remove old completed or failed executions
      if (
        (execution.status === 'completed' || execution.status === 'failed') &&
        completedAt &&
        now - completedAt > maxAgeMs
      ) {
        delete this.queue!.executions[id];
        removed++;
      }
    }

    if (removed > 0) {
      await this.save();
    }

    return removed;
  }

  /**
   * Get statistics about the queue.
   */
  async getStats(): Promise<{
    total: number;
    pending: number;
    running: number;
    completed: number;
    failed: number;
  }> {
    if (!this.queue) await this.initialize();

    const executions = Object.values(this.queue!.executions);
    return {
      total: executions.length,
      pending: executions.filter(e => e.status === 'pending').length,
      running: executions.filter(e => e.status === 'running').length,
      completed: executions.filter(e => e.status === 'completed').length,
      failed: executions.filter(e => e.status === 'failed').length,
    };
  }

  /**
   * Export all data (for debugging).
   */
  async export(): Promise<ExecutionQueue | null> {
    if (!this.queue) await this.initialize();
    return this.queue;
  }
}

/**
 * Singleton instance for the application.
 */
let globalStore: ExecutionStore | null = null;

export function getExecutionStore(workDir?: string): ExecutionStore {
  if (!globalStore) {
    globalStore = new ExecutionStore(workDir);
  }
  return globalStore;
}
