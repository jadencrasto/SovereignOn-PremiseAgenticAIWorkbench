import { apiRequest } from './client';
import type { TaskListResponse, TaskDetail, ApprovalRequest } from '../types';

/**
 * Phase 6: Fetch the list of recent agent tasks.
 */
export async function fetchTasks(
  limit: number = 50,
  status?: string
): Promise<TaskListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (status) params.set('status', status);
  return apiRequest<TaskListResponse>(`/api/tasks?${params.toString()}`);
}

/**
 * Phase 6: Fetch full detail for a specific task.
 */
export async function fetchTask(taskId: string): Promise<TaskDetail> {
  return apiRequest<TaskDetail>(`/api/tasks/${encodeURIComponent(taskId)}`);
}

/**
 * Phase 6: Approve a pending step in a task.
 * Returns an SSE stream (handled via fetch, not apiRequest).
 */
export async function approveTaskStep(taskId: string): Promise<Response> {
  return fetch(`/api/tasks/${encodeURIComponent(taskId)}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'approve' }),
  });
}

/**
 * Phase 6: Reject a pending step in a task.
 * Returns an SSE stream (handled via fetch, not apiRequest).
 */
export async function rejectTaskStep(
  taskId: string,
  reason: string = ''
): Promise<Response> {
  return fetch(`/api/tasks/${encodeURIComponent(taskId)}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'reject', reason }),
  });
}

/**
 * Phase 6: Cancel a running or paused task.
 */
export async function cancelTask(
  taskId: string
): Promise<{ task_id: string; status: string; message: string }> {
  return apiRequest<{ task_id: string; status: string; message: string }>(
    `/api/tasks/${encodeURIComponent(taskId)}/cancel`,
    { method: 'POST' }
  );
}

/**
 * Phase 6: Fetch approval history for a task.
 */
export async function fetchTaskApprovals(
  taskId: string
): Promise<{ approvals: ApprovalRequest[]; total: number }> {
  return apiRequest<{ approvals: ApprovalRequest[]; total: number }>(
    `/api/tasks/${encodeURIComponent(taskId)}/approvals`
  );
}
