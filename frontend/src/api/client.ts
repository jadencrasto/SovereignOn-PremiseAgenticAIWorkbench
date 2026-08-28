import type { StreamChunkPayload } from '../types';

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
