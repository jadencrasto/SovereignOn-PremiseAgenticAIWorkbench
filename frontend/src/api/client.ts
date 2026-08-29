import type { StreamChunkPayload, ToolEvent } from '../types';

export class ApiError extends Error {
  status: number;
  data: any;

  constructor(message: string, status: number, data?: any) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

export async function apiRequest<T>(
  url: string,
  options: RequestInit = {}
): Promise<T> {
  const defaultHeaders: Record<string, string> = {
    Accept: 'application/json',
  };

  if (!(options.body instanceof FormData)) {
    defaultHeaders['Content-Type'] = 'application/json';
  }

  const response = await fetch(url, {
    ...options,
    headers: {
      ...defaultHeaders,
      ...options.headers,
    },
  });

  if (!response.ok) {
    let errorDetail = `Request failed with status ${response.status}`;
    let errorData = null;
    try {
      errorData = await response.json();
      if (errorData?.detail) {
        errorDetail = typeof errorData.detail === 'string' 
          ? errorData.detail 
          : JSON.stringify(errorData.detail);
      }
    } catch {
      // Non-JSON error
    }
    throw new ApiError(errorDetail, response.status, errorData);
  }

  return response.json();
}

export async function streamSSE(
  url: string,
  payload: any,
  callbacks: {
    onDelta: (delta: string) => void;
    onSources: (sources: any[]) => void;
    onDone: (sessionId: string, modelUsed: string) => void;
    onError: (error: string) => void;
    onToolEvent?: (event: ToolEvent) => void;
    // Phase 5: vision status events
    onAgentStatus?: (status: string) => void;
    // Phase 6: planning / approval / task callbacks
    onPlanCreated?: (planData: any) => void;
    onPlanStep?: (stepData: any) => void;
    onApprovalRequired?: (approvalData: any) => void;
    onApprovalResolved?: (status: string, approvalData?: any) => void;
    onTaskStatus?: (status: string, taskId: string) => void;
  },
  signal?: AbortSignal
): Promise<void> {
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok) {
    let errorMsg = `Server error ${response.status}`;
    try {
      const err = await response.json();
      if (err?.detail) errorMsg = err.detail;
    } catch {}
    callbacks.onError(errorMsg);
    return;
  }

  if (!response.body) {
    callbacks.onError('ReadableStream not supported by browser or empty response');
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) continue;

        const jsonStr = trimmed.slice(6);
        try {
          const chunk: StreamChunkPayload = JSON.parse(jsonStr);

          if (chunk.type === 'delta') {
            if (chunk.content) {
              callbacks.onDelta(chunk.content);
            }
          } else if (chunk.type === 'sources') {
            if (chunk.sources && Array.isArray(chunk.sources)) {
              callbacks.onSources(chunk.sources);
            }
          } else if (chunk.type === 'done') {
            callbacks.onDone(chunk.session_id || '', chunk.model_used || '');
          } else if (chunk.type === 'error') {
            callbacks.onError(chunk.content || 'An error occurred during generation');
          } else if (chunk.type === 'tool_start' || chunk.type === 'tool_result') {
            // Phase 4: Tool events
            if (callbacks.onToolEvent) {
              const toolEvent: ToolEvent = {
                type: chunk.type,
                tool: chunk.tool || '',
                arguments: chunk.tool_args,
                success: chunk.success,
                summary: chunk.summary,
              };
              callbacks.onToolEvent(toolEvent);
            }
          } else if (chunk.type === 'agent_status') {
            // Phase 5: vision/agent status updates
            if (callbacks.onAgentStatus) {
              callbacks.onAgentStatus(chunk.content || 'thinking');
            }
          } else if (chunk.type === 'plan_created') {
            // Phase 6: Plan created event
            if (callbacks.onPlanCreated) {
              callbacks.onPlanCreated(chunk);
            }
          } else if (chunk.type === 'plan_step') {
            // Phase 6: Plan step update
            if (callbacks.onPlanStep) {
              callbacks.onPlanStep(chunk);
            }
          } else if (chunk.type === 'approval_required') {
            // Phase 6: Approval required
            if (callbacks.onApprovalRequired) {
              callbacks.onApprovalRequired(chunk);
            }
          } else if (chunk.type === 'approval_granted' || chunk.type === 'approval_rejected') {
            // Phase 6: Approval resolved
            if (callbacks.onApprovalResolved) {
              callbacks.onApprovalResolved(chunk.type, chunk);
            }
          } else if (chunk.type === 'task_started' || chunk.type === 'task_completed' || chunk.type === 'task_failed' || chunk.type === 'task_cancelled') {
            // Phase 6: Task lifecycle event
            if (callbacks.onTaskStatus) {
              callbacks.onTaskStatus(chunk.type, chunk.task_id || '');
            }
          }
        } catch (parseError) {
          console.warn('Failed to parse SSE line:', jsonStr, parseError);
        }
      }
    }
  } catch (err: any) {
    if (err.name === 'AbortError') {
      console.log('Stream aborted by client');
    } else {
      callbacks.onError(err.message || 'Stream connection lost');
    }
  } finally {
    reader.releaseLock();
  }
}

/**
 * Phase 5: SSE streaming via multipart/form-data.
 *
 * Sends a FormData body (with image) to the multimodal endpoint and
 * parses the SSE response using the same reader logic as streamSSE.
 * Do NOT set Content-Type — the browser adds it with the multipart boundary.
 */
export async function streamSSEFromFormData(
  url: string,
  formData: FormData,
  callbacks: {
    onDelta: (delta: string) => void;
    onSources: (sources: any[]) => void;
    onDone: (sessionId: string, modelUsed: string) => void;
    onError: (error: string) => void;
    onToolEvent?: (event: ToolEvent) => void;
    onAgentStatus?: (status: string) => void;
    onPlanCreated?: (planData: any) => void;
    onPlanStep?: (stepData: any) => void;
    onApprovalRequired?: (approvalData: any) => void;
    onApprovalResolved?: (status: string, approvalData?: any) => void;
    onTaskStatus?: (status: string, taskId: string) => void;
  },
  signal?: AbortSignal
): Promise<void> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { Accept: 'text/event-stream' },
    body: formData,
    signal,
  });

  if (!response.ok) {
    let errorMsg = `Server error ${response.status}`;
    try {
      const err = await response.json();
      if (err?.detail) errorMsg = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail);
    } catch {}
    callbacks.onError(errorMsg);
    return;
  }

  if (!response.body) {
    callbacks.onError('ReadableStream not supported or empty response');
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) continue;
        const jsonStr = trimmed.slice(6);
        try {
          const chunk: StreamChunkPayload = JSON.parse(jsonStr);
          if (chunk.type === 'delta') {
            if (chunk.content) callbacks.onDelta(chunk.content);
          } else if (chunk.type === 'sources') {
            if (chunk.sources && Array.isArray(chunk.sources)) callbacks.onSources(chunk.sources);
          } else if (chunk.type === 'done') {
            callbacks.onDone(chunk.session_id || '', chunk.model_used || '');
          } else if (chunk.type === 'error') {
            callbacks.onError(chunk.content || 'An error occurred during generation');
          } else if (chunk.type === 'tool_start' || chunk.type === 'tool_result') {
            if (callbacks.onToolEvent) {
              callbacks.onToolEvent({
                type: chunk.type,
                tool: chunk.tool || '',
                arguments: chunk.tool_args,
                success: chunk.success,
                summary: chunk.summary,
              });
            }
          } else if (chunk.type === 'agent_status') {
            if (callbacks.onAgentStatus) callbacks.onAgentStatus(chunk.content || 'thinking');
          } else if (chunk.type === 'plan_created') {
            if (callbacks.onPlanCreated) callbacks.onPlanCreated(chunk);
          } else if (chunk.type === 'plan_step') {
            if (callbacks.onPlanStep) callbacks.onPlanStep(chunk);
          } else if (chunk.type === 'approval_required') {
            if (callbacks.onApprovalRequired) callbacks.onApprovalRequired(chunk);
          } else if (chunk.type === 'approval_granted' || chunk.type === 'approval_rejected') {
            if (callbacks.onApprovalResolved) callbacks.onApprovalResolved(chunk.type, chunk);
          } else if (chunk.type === 'task_started' || chunk.type === 'task_completed' || chunk.type === 'task_failed' || chunk.type === 'task_cancelled') {
            if (callbacks.onTaskStatus) callbacks.onTaskStatus(chunk.type, chunk.task_id || '');
          }
        } catch (parseError) {
          console.warn('Failed to parse SSE line:', jsonStr, parseError);
        }
      }
    }
  } catch (err: any) {
    if (err.name === 'AbortError') {
      console.log('Multimodal stream aborted');
    } else {
      callbacks.onError(err.message || 'Stream connection lost');
    }
  } finally {
    reader.releaseLock();
  }
}
