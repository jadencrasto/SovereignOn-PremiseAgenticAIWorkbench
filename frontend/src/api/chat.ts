import { apiRequest, streamSSE } from './client';
import type {
  ChatRequestPayload,
  ChatResponsePayload,
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

export async function fetchSessions(): Promise<{ sessions: string[]; count: number }> {
  return apiRequest<{ sessions: string[]; count: number }>('/api/chat/sessions');
}

export async function clearSession(sessionId: string): Promise<{ deleted: string }> {
  return apiRequest<{ deleted: string }>(`/api/chat/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  });
}
