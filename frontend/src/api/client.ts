import type { StreamChunkPayload, StreamErrorInfo, ToolEvent } from '../types';

export class ApiError extends Error {
  status: number;
  data: any;
  code?: string;

  constructor(message: string, status: number, data?: any, code?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
    this.code = code;
  }
}

export async function apiRequest<T>(
  url: string,
  options: RequestInit = {}
): Promise<T> {
  const defaultHeaders: Record<string, string> = {
    Accept: 'application/json',
    'X-Requested-With': 'XMLHttpRequest',
  };

  if (!(options.body instanceof FormData)) {
    defaultHeaders['Content-Type'] = 'application/json';
  }

  const response = await fetch(url, {
    ...options,
    credentials: options.credentials ?? 'include',
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
    throw new ApiError(errorDetail, response.status, errorData, response.headers?.get('X-Error-Code') ?? undefined);
  }

  return response.json();
}

async function streamErrorMessage(response: Response): Promise<string> {
  try {
    const error = await response.json();
    if (typeof error?.detail === 'string' && error.detail.trim()) return error.detail;
  } catch {
    // A proxy failure can return a non-JSON body.
  }
  if (response.status === 413) return 'The upload is too large.';
  return response.status === 422 ? 'The chat request is invalid.' : `Server error ${response.status}`;
}

export async function streamSSE(
  url: string,
  payload: any,
  callbacks: {
    onDelta: (delta: string) => void;
    onSources: (sources: any[]) => void;
    onDone: (sessionId: string, modelUsed: string, attachment?: StreamChunkPayload['attachment'], fileSources?: StreamChunkPayload['file_sources']) => void;
    onError: (error: string, info?: StreamErrorInfo) => void;
    onAccepted?: () => void;
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
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      'X-Requested-With': 'XMLHttpRequest',
    },
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok) {
    callbacks.onError(await streamErrorMessage(response), {
      status: response.status, code: response.headers?.get('X-Error-Code') ?? undefined,
    });
    return;
  }

  if (!response.body) {
    callbacks.onError('ReadableStream not supported by browser or empty response');
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let terminal = false;
  let sequence = 0;
  const identified = typeof payload.turn_id === 'string';

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
          if (terminal) continue;

          if (identified) {
            if (chunk.session_id !== payload.session_id || chunk.turn_id !== payload.turn_id ||
                chunk.attempt !== payload.attempt || typeof chunk.sequence !== 'number' ||
                !Number.isSafeInteger(chunk.sequence) || chunk.sequence < 1 ||
                typeof chunk.content !== 'string') throw new Error('Invalid turn event');
            if (chunk.sequence <= sequence) continue;
            if (chunk.sequence !== sequence + 1 ||
                !(chunk.type === 'done' || chunk.type === 'error' ||
                  (sequence === 0 ? chunk.type === 'accepted' : chunk.type === 'delta'))) {
              throw new Error('Out-of-order turn event');
            }
            sequence = chunk.sequence;
          }

          if (chunk.type === 'accepted') {
            if (identified) callbacks.onAccepted?.();
          } else if (chunk.type === 'delta') {
            if (chunk.content) {
              callbacks.onDelta(chunk.content);
            }
          } else if (chunk.type === 'sources') {
            if (chunk.sources && Array.isArray(chunk.sources)) {
              callbacks.onSources(chunk.sources);
            }
          } else if (chunk.type === 'done') {
            if (terminal) continue;
            terminal = true;
            callbacks.onDone(chunk.session_id || '', chunk.model_used || '');
          } else if (chunk.type === 'error') {
            if (terminal) continue;
            terminal = true;
            callbacks.onError(chunk.content || 'An error occurred during generation', { code: chunk.error_code });
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
            if (chunk.type === 'task_failed' || chunk.type === 'task_cancelled') terminal = true;
            if (callbacks.onTaskStatus) {
              callbacks.onTaskStatus(chunk.type, chunk.task_id || chunk.content || '');
            }
          }
        } catch (parseError) {
          if (!terminal) { terminal = true; callbacks.onError('Invalid stream response'); }
        }
      }
      if (terminal) {
        // Do not leave an HTTP stream open after its terminal event.
        await reader.cancel().catch(() => {});
        break;
      }
    }
    if (!terminal && !signal?.aborted) callbacks.onError('Response interrupted before completion');
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
    onDone: (sessionId: string, modelUsed: string, attachment?: StreamChunkPayload['attachment'], fileSources?: StreamChunkPayload['file_sources']) => void;
    onError: (error: string, info?: StreamErrorInfo) => void;
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
    credentials: 'include',
    headers: {
      Accept: 'text/event-stream',
      'X-Requested-With': 'XMLHttpRequest',
    },
    body: formData,
    signal,
  });

  if (!response.ok) {
    callbacks.onError(await streamErrorMessage(response), {
      status: response.status, code: response.headers?.get('X-Error-Code') ?? undefined,
    });
    return;
  }

  if (!response.body) {
    callbacks.onError('ReadableStream not supported or empty response');
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let terminal = false;

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
          if (terminal) continue;
          if (chunk.type === 'delta') {
            if (chunk.content) callbacks.onDelta(chunk.content);
          } else if (chunk.type === 'sources') {
            if (chunk.sources && Array.isArray(chunk.sources)) callbacks.onSources(chunk.sources);
          } else if (chunk.type === 'done') {
            if (terminal) continue;
            terminal = true;
            if (chunk.file_sources === undefined) {
              callbacks.onDone(chunk.session_id || '', chunk.model_used || '', chunk.attachment ?? undefined);
            } else {
              callbacks.onDone(chunk.session_id || '', chunk.model_used || '', chunk.attachment ?? undefined, chunk.file_sources);
            }
          } else if (chunk.type === 'error') {
            if (terminal) continue;
            terminal = true;
            callbacks.onError(chunk.content || 'An error occurred during generation', { code: chunk.error_code });
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
            if (chunk.type === 'task_failed' || chunk.type === 'task_cancelled') terminal = true;
            if (callbacks.onTaskStatus) callbacks.onTaskStatus(chunk.type, chunk.task_id || chunk.content || '');
          }
        } catch (parseError) {
          if (!terminal) { terminal = true; callbacks.onError('Invalid stream response'); }
        }
      }
      if (terminal) {
        // Do not leave an HTTP stream open after its terminal event.
        await reader.cancel().catch(() => {});
        break;
      }
    }
    if (!terminal && !signal?.aborted) callbacks.onError('Response interrupted before completion', { code: 'STREAM_CONNECTION_LOST' });
  } catch (err: any) {
    if (err.name === 'AbortError') {
      console.log('Multimodal stream aborted');
    } else {
      callbacks.onError(err.message || 'Stream connection lost', { code: 'STREAM_CONNECTION_LOST' });
    }
  } finally {
    reader.releaseLock();
  }
}
