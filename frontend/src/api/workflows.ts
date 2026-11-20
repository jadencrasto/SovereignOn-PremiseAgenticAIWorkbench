import { apiRequest } from './client';

export type WorkflowState = 'running' | 'waiting_clarification' | 'completed' | 'failed' | 'interrupted' | 'cancelled';
export interface WorkflowSummary {
  id: string;
  session_id: string;
  conversation_title: string;
  objective: string;
  state: WorkflowState;
  created_at: string;
  updated_at: string;
  has_draft?: boolean;
  export_recorded?: boolean;
}
export interface WorkflowDetail extends WorkflowSummary {
  attempt: number;
  plan_revision: number | null;
  steps: string[];
  last_error: string | null;
  sources: { id: string; label: string; revision: number; current: boolean; reusable: boolean }[];
  image: { label: string; current: boolean } | null;
  dependencies_current: boolean;
  clarification: { question: string } | null;
  drafts: { id: string; title: string; format: string; revision: number; revisions: number[];
    stale: boolean; cancelled: boolean; pending_revision: boolean; approved: boolean; downloadable: boolean }[];
  can_resume: boolean;
  can_retry: boolean;
  can_reuse: boolean;
}
export interface ReusableWorkflow {
  turn_id: string;
  message: string;
  knowledge_source_ids: string[] | null;
  requires_image_selection: boolean;
}

const route = (item: WorkflowSummary) =>
  `/api/chat/sessions/${encodeURIComponent(item.session_id)}/workflows/${encodeURIComponent(item.id)}`;

export function listWorkflows(status: string, query: string, offset: number, signal: AbortSignal) {
  const params = new URLSearchParams({ status, q: query, offset: String(offset) });
  return apiRequest<{ workflows: WorkflowSummary[]; next_offset: number | null }>(
    `/api/chat/workflows?${params}`, { cache: 'no-store', signal });
}

export function readWorkflow(item: WorkflowSummary, signal: AbortSignal) {
  return apiRequest<WorkflowDetail>(route(item), { cache: 'no-store', signal });
}

export function prepareWorkflowReuse(item: WorkflowSummary, sources: string[], signal: AbortSignal) {
  return apiRequest<ReusableWorkflow>(route(item) + '/reuse', {
    method: 'POST', cache: 'no-store', signal, body: JSON.stringify({ source_ids: sources.length ? sources : null }),
  });
}

export function workflowDownload(item: WorkflowSummary, draft: WorkflowDetail['drafts'][number]) {
  return `/api/chat/sessions/${encodeURIComponent(item.session_id)}/drafts/${encodeURIComponent(draft.id)}/download?revision=${draft.revision}`;
}
