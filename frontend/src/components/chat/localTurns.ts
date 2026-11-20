import type { ChatMessage } from '../../types';

export interface LocalSubmission {
  session_id: string;
  turn_id: string;
  attempt: number;
  message: string;
  model: string;
  tools_enabled?: false;
  knowledge_source_ids?: string[];
  image_id?: string;
  draft_turn_ids?: string[];
  workflow_preview?: string;
}

export function isLocalDraftRequest(text: string): boolean {
  const clauses = text.trim().split(/(?:,?\s+and\s+(?:then\s+)?|[.;]\s+|,\s+then\s+)(?=(?:create|draft|prepare|make|generate|write|build)\b)/i);
  const clause = (clauses[1] ?? clauses[0]).replace(
    /^(?:use|using)\s+[^\n]{0,270}\b(?:image|photo|picture|documents?|sources?)\b[^\n]{0,29}?\s+to\s+/i, '');
  return /^(?:please\s+)?(?:create|draft|prepare|make|generate|write|build)\s+(?:(?:a|an|the|me)\s+){0,2}(?:(?:cited|short|observation|maintenance|inspection|comparison|evidence[- ]grounded)\s+){0,3}(?:report|spreadsheet|summary|document|docx|xlsx|pdf)\b/i.test(clause);
}

export function needsWorkflowPreview(text: string, scoped: boolean): boolean {
  return isLocalDraftRequest(text) || (/^\s*(?:please\s+)?(?:compare|contrast|analy[sz]e)\b/i.test(text)
    && (scoped || /\b(?:reports?|documents?|sources?|files?|records?|workbooks?)\b/i.test(text)));
}

export function createLocalSubmission(sessionId: string, message: string, model: string): LocalSubmission {
  return {
    session_id: sessionId,
    turn_id: crypto.randomUUID(),
    attempt: 1,
    message,
    model,
  };
}

export function retryLocalSubmission(sessionId: string, message?: ChatMessage): LocalSubmission | null {
  const turn = message?.localTurn;
  if (!message || !turn || message.withheld || turn.state === 'completed' || turn.state === 'running' ||
      turn.state === 'waiting_clarification' || turn.knowledge_scope_stale ||
      ['LOCAL_KNOWLEDGE_CHANGED', 'LOCAL_TOOL_UNAUTHORIZED', 'CONVERSATION_UNAVAILABLE',
        'LOCAL_WORKFLOW_CANCELLED'].includes(turn.error_code ?? '') ||
      (turn.plan_revision !== undefined && turn.attempt >= 3)) return null;
  const retry: LocalSubmission = { session_id: sessionId, turn_id: message.id, attempt: turn.attempt + 1,
    message: message.content, model: turn.model };
  if (turn.tools_enabled === false) retry.tools_enabled = false;
  if (turn.image_id) {
    if (!message.attachments?.some(file => file.id === turn.image_id && file.localAvailable === true)) return null;
    retry.image_id = turn.image_id;
  }
  if (turn.knowledge_scope?.mode === 'selected') {
    retry.knowledge_source_ids = turn.knowledge_scope.sources.map(source => source.source_id);
  }
  if (turn.draft_turn_ids) retry.draft_turn_ids = turn.draft_turn_ids;
  return retry;
}

export function reconcileLocalSubmission(messages: ChatMessage[], pending: LocalSubmission | null): LocalSubmission | null {
  if (!pending) return null;
  const observed = messages.find(message => message.id === pending.turn_id && message.role === 'user')?.localTurn;
  return observed && observed.attempt >= pending.attempt ? null : pending;
}

export function latestLocalUser(messages: ChatMessage[]): ChatMessage | undefined {
  return [...messages].reverse().find(message => message.role === 'user' && !message.withheld && Boolean(message.localTurn));
}
