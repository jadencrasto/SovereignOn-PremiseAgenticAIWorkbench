import type { ChatMessage } from '../../types';

export interface LocalSubmission {
  session_id: string;
  turn_id: string;
  attempt: number;
  message: string;
  model: string;
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
  if (!message || !turn || message.withheld || turn.state === 'completed' || turn.state === 'running') return null;
  return { session_id: sessionId, turn_id: message.id, attempt: turn.attempt + 1,
    message: message.content, model: turn.model };
}

export function reconcileLocalSubmission(messages: ChatMessage[], pending: LocalSubmission | null): LocalSubmission | null {
  if (!pending) return null;
  const observed = messages.find(message => message.id === pending.turn_id && message.role === 'user')?.localTurn;
  return observed && observed.attempt >= pending.attempt ? null : pending;
}

export function latestLocalUser(messages: ChatMessage[]): ChatMessage | undefined {
  return [...messages].reverse().find(message => message.role === 'user' && !message.withheld && Boolean(message.localTurn));
}
