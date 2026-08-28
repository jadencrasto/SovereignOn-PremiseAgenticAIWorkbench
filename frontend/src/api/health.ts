import { apiRequest } from './client';
import type { HealthResponse } from '../types';

export async function checkHealth(): Promise<HealthResponse> {
  return apiRequest<HealthResponse>('/api/health');
}
