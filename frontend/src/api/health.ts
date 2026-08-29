import { apiRequest } from './client';
import type { HealthResponse, ReadinessResponse } from '../types';

export async function checkHealth(): Promise<HealthResponse> {
  return apiRequest<HealthResponse>('/api/health');
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
