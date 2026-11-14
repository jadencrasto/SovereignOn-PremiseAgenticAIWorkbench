import { parseCapabilityReport } from "./capabilities";
import { apiRequest } from './client';
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

export async function fetchCapabilities(checkOllama = false, model = '') {
  const path = (checkOllama ? '/api/health/capabilities/ollama-check' : '/api/health/capabilities') +
    (model ? `?model=${encodeURIComponent(model)}` : '');
  return parseCapabilityReport(await apiRequest<unknown>(path, {
    method: checkOllama ? 'POST' : 'GET',
    signal: AbortSignal.timeout(5000),
    cache: 'no-store',
  }));
}

export async function checkModelInference(model: string) {
  return parseCapabilityReport(await apiRequest<unknown>('/api/health/capabilities/inference-check', {
    method: 'POST', body: JSON.stringify({ model }), signal: AbortSignal.timeout(35000), cache: 'no-store',
  }));
}
