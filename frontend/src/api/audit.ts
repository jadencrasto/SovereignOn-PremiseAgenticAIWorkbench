/**
 * frontend/src/api/audit.ts
 * --------------------------
 * Centralized Audit API client.
 */

import type { AuditListResponse, AuditSummary } from '../types';

const BASE_URL = '/api/audit';

export async function fetchAuditEventsApi(params: {
  limit?: number;
  offset?: number;
  user_id?: string;
  event_type?: string;
  task_id?: string;
  tool?: string;
  success?: boolean;
}): Promise<AuditListResponse> {
  const query = new URLSearchParams();
  if (params.limit) query.set('limit', String(params.limit));
  if (params.offset !== undefined) query.set('offset', String(params.offset));
  if (params.user_id) query.set('user_id', params.user_id);
  if (params.event_type) query.set('event_type', params.event_type);
  if (params.task_id) query.set('task_id', params.task_id);
  if (params.tool) query.set('tool', params.tool);
  if (params.success !== undefined) query.set('success', String(params.success));

  const resp = await fetch(`${BASE_URL}/events?${query.toString()}`, {
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  });

  if (!resp.ok) {
    throw new Error(`Failed to load audit events (${resp.status})`);
  }

  return resp.json();
}

export async function fetchAuditSummaryApi(): Promise<AuditSummary> {
  const resp = await fetch(`${BASE_URL}/summary`, {
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  });

  if (!resp.ok) {
    throw new Error(`Failed to load audit summary (${resp.status})`);
  }

  return resp.json();
}

export async function pruneAuditLogApi(): Promise<{ message: string; deleted_rows: number }> {
  const resp = await fetch(`${BASE_URL}/prune`, {
    method: 'POST',
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  });

  if (!resp.ok) {
    throw new Error(`Failed to prune audit log (${resp.status})`);
  }

  return resp.json();
}
