import { apiRequest } from './client';
import type { ModelsResponse } from '../types';

export async function fetchModels(): Promise<ModelsResponse> {
  return apiRequest<ModelsResponse>('/api/models');
}

export async function fetchDefaultModel(): Promise<{ default_model: string }> {
  return apiRequest<{ default_model: string }>('/api/models/default');
}
