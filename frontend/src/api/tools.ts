import { apiRequest } from './client';
import type { ToolsListResponse } from '../types';

export async function fetchTools(): Promise<ToolsListResponse> {
  return apiRequest<ToolsListResponse>('/api/tools');
}
