import { parseCapabilityReport } from "./capabilities";
import { ApiError, apiRequest } from './client';
import type { HealthResponse, ReadinessResponse } from '../types';

export async function checkHealth(): Promise<HealthResponse> {
  return apiRequest<HealthResponse>('/api/health', { signal: AbortSignal.timeout(5000), cache: 'no-store' });
}

export async function fetchSystemReadinessApi(): Promise<ReadinessResponse> {
  const resp = await fetch('/api/health/ready', {
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  });
  if (!resp.ok && resp.status !== 503) {
    throw new Error(`Health probe returned error (${resp.status})`);
  }
  return resp.json();
}

export async function fetchCapabilities(checkOllama = false, model = '', visionModel = '') {
  const query = new URLSearchParams();
  if (model) query.set('model', model);
  if (visionModel) query.set('vision_model', visionModel);
  const path = (checkOllama ? '/api/health/capabilities/ollama-check' : '/api/health/capabilities') +
    (query.size ? `?${query}` : '');
  return requestCapabilityReport(path, {
    method: checkOllama ? 'POST' : 'GET',
    signal: AbortSignal.timeout(5000),
    cache: 'no-store',
  });
}

export async function checkModelInference(model: string, visionModel = '') {
  return requestCapabilityReport('/api/health/capabilities/inference-check' +
    (visionModel ? `?vision_model=${encodeURIComponent(visionModel)}` : ''), {
    method: 'POST', body: JSON.stringify({ model }), signal: AbortSignal.timeout(35000), cache: 'no-store',
  }, 'chat');
}

export async function checkVisionInference(visionModel: string, model = '') {
  return requestCapabilityReport('/api/health/capabilities/vision-check' +
    (model ? `?model=${encodeURIComponent(model)}` : ''), {
    method: 'POST', body: JSON.stringify({ model: visionModel }), signal: AbortSignal.timeout(35000), cache: 'no-store',
  }, 'vision');
}

async function requestCapabilityReport(path: string, options: RequestInit, target?: 'chat' | 'vision') {
  try {
    return parseCapabilityReport(await apiRequest<unknown>(path, options));
  } catch (error) {
    // Conflict responses can carry current inventory and a specific readiness reason.
    if (!(error instanceof ApiError) || error.status !== 409 || options.method !== 'POST') throw error;
    const report = parseCapabilityReport(error.data);
    if ((target !== 'vision' && (report.capabilities.chat.state === 'available' || report.selected_chat_model?.state === 'available')) ||
        (target !== 'chat' && (report.capabilities.multimodal.state === 'available' || report.selected_vision_model?.state === 'available'))) {
      throw error;
    }
    return report;
  }
}
