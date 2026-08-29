import { apiRequest, streamSSE, streamSSEFromFormData } from './client';
import type {
  ChatRequestPayload,
  ChatResponsePayload,
  MultimodalChatRequestPayload,
  SourceReference,
  ToolEvent,
} from '../types';

export async function sendChatSync(
  payload: ChatRequestPayload
): Promise<ChatResponsePayload> {
  return apiRequest<ChatResponsePayload>('/api/chat', {
    method: 'POST',
    body: JSON.stringify({ ...payload, stream: false }),
  });
}

export async function streamChat(
  payload: ChatRequestPayload,
  callbacks: {
    onDelta: (delta: string) => void;
    onSources: (sources: SourceReference[]) => void;
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
  return streamSSE(
    '/api/chat',
    { ...payload, stream: true },
    callbacks,
    signal
  );
}

/**
 * Phase 5: Multimodal streaming chat.
 *
 * When payload.image is present, sends multipart/form-data to
 * /api/chat/multimodal which routes through the two-step vision pipeline
 * (LLaVA → qwen2.5:7b tool loop).
 *
 * When no image is provided, falls back to the standard JSON /api/chat path.
 */
export async function streamChatMultimodal(
  payload: MultimodalChatRequestPayload,
  callbacks: {
    onDelta: (delta: string) => void;
    onSources: (sources: SourceReference[]) => void;
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
  if (!payload.image) {
    // No image — use the standard text-only path
    return streamSSE(
      '/api/chat',
      {
        session_id: payload.session_id,
        message: payload.message,
        model: payload.model,
        stream: true,
        tools_enabled: payload.tools_enabled,
      },
      callbacks,
      signal
    );
  }

  // Image attached — use multipart endpoint
  const formData = new FormData();
  formData.append('message', payload.message);
  if (payload.session_id) formData.append('session_id', payload.session_id);
  if (payload.model) formData.append('model', payload.model);
  formData.append('stream', 'true');
  formData.append('tools_enabled', String(payload.tools_enabled ?? true));
  formData.append('image', payload.image, payload.image.name);

  return streamSSEFromFormData('/api/chat/multimodal', formData, callbacks, signal);
}

export async function fetchSessions(): Promise<{ sessions: string[]; count: number }> {
  return apiRequest<{ sessions: string[]; count: number }>('/api/chat/sessions');
}

export async function clearSession(sessionId: string): Promise<{ deleted: string }> {
  return apiRequest<{ deleted: string }>(`/api/chat/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  });
}

