/**
 * frontend/src/components/chat/ChatView.tsx
 * ------------------------------------------
 * Main Chat & Agent Conversation Interface with Persistent Multi-Session History,
 * Visual Explanations (Mermaid, Callouts, Tables), and Resumable Workflows.
 */

import React, { useState, useRef, useCallback, useEffect } from 'react';
import type { ChatMessage, ImageAttachment, SourceReference, StreamChunkPayload, StreamErrorInfo, ToolEvent } from '../../types';
import { MessageList } from './MessageList';
import { ChatInput } from './ChatInput';
import { useWorkbench } from '../../context/WorkbenchContext';
import {
  createSession, fetchSessions, streamChat, streamChatMultimodal, streamChatTextFile, streamChatTextFiles,
  cancelLocalTurn, guardChatCallbacks, type TextFileBatch, type TextFileLineRange,
} from '../../api/chat';
import { useAuth } from '../../context/AuthContext';
import { capabilityFor, chatDisabled, describeCapability, permitsCapability, modelInInventory } from '../../api/capabilities';
import { approveTaskStep, rejectTaskStep } from '../../api/tasks';
import {
  ChatHistorySidebar, loadSession,
  type ChatSession,
} from './ChatHistorySidebar';
import { latestLocalUser, reconcileLocalSubmission, retryLocalSubmission, type LocalSubmission } from './localTurns';
import {
  Clock,
  Plus,
} from 'lucide-react';

function fileTurnError(message: string, info?: StreamErrorInfo): string {
  switch (info?.code) {
    case 'TEXT_FILE_REATTACH_REQUIRED':
      return 'Reattach the TXT, CSV or JSON sources and restate any previous filters or selections in your question.';
    case 'TEXT_SOURCE_REQUIRED':
    case 'TEXT_SOURCE_AMBIGUOUS':
    case 'TEXT_SOURCE_CONFLICT':
    case 'TEXT_SOURCE_INVALID':
      return message || 'Attach the files again and choose the sources needed for your question.';
    case 'TEXT_CROSS_SOURCE_UNSUPPORTED':
      return 'Attach TXT limits and CSV or JSON readings for a comparison, or ask about one source.';
    case 'TEXT_SOURCE_OPERATION_UNSUPPORTED':
      return 'Ask for a supported calculation or passage lookup on one attached source.';
    case 'TEXT_ATTACHMENTS_TOO_LARGE':
      return 'The combined files exceed 4 KiB. Attach smaller files.';
    case 'TEXT_FORM_TOO_LARGE':
      return 'The combined upload request is too large. Attach smaller files.';
    case 'TEXT_SOURCE_UNVERIFIED':
      return 'The result could not be matched to the selected sources. Attach the files again to retry.';
    case 'TEXT_WORKFLOW_STEP_FAILED':
    case 'TEXT_WORKFLOW_TIMEOUT':
      // The attachment runner exposes a public step description and verified summary.
      return message;
    case 'TEXT_WORKFLOW_LIMIT':
      return 'This analysis exceeds the step limit. Ask for a shorter workflow.';
    case 'MODEL_NOT_READY':
      return 'Check the selected text model in Models before trying again.';
    case 'CSV_HEADER_INVALID':
    case 'CSV_ROW_WIDTH':
    case 'CSV_INVALID':
    case 'CSV_EMPTY':
      return 'The CSV needs valid comma-delimited rows and unique headers. Check it and attach it again.';
    case 'JSON_DUPLICATE_KEY':
      return 'JSON keys must be unique in each object.';
    case 'JSON_INVALID':
    case 'JSON_SHAPE':
    case 'JSON_LIMIT':
    case 'JSON_AMBIGUOUS_FIELD':
    case 'JSON_NUMBER':
      return 'Check the JSON format, unique fields, and supported record and number limits.';
    case 'CSV_RANGE_INVALID':
    case 'TEXT_FILE_RANGE_INVALID':
      return 'Choose and preview a valid range before submitting.';
    case 'STREAM_CONNECTION_LOST':
      return 'The file analysis connection was interrupted. Reattach the file to try again.';
  }
  if (info?.status === 403) return 'You do not have access to file analysis in this session.';
  if (info?.status === 413) return 'The file exceeds the 2 KiB or decoded text limit.';
  if (info?.status === 415) return 'Choose a supported UTF-8 TXT, CSV or JSON file.';
  if (info?.status === 400 || info?.status === 422) return 'Check the file and selected range, then attach it again.';
  return 'We could not answer the file question. Reattach the file to try again.';
}

function fileAttachmentMime(filename: string) {
  const name = filename.toLowerCase();
  return name.endsWith('.json') ? 'application/json' : name.endsWith('.csv') ? 'text/csv' : 'text/plain';
}

function matchesComparisonSources(batch: TextFileBatch, sources: NonNullable<StreamChunkPayload['file_sources']>): boolean {
  if (!Array.isArray(sources) || sources.length !== 2) return false;
  const [limits, measurements] = sources;
  if (limits?.role !== 'limits' || limits.mime_type !== 'text/plain' ||
      measurements?.role !== 'measurements' ||
      !['text/csv', 'application/json'].includes(measurements.mime_type) ||
      measurements.attachment_id !== 'source-' + batch.source) return false;
  const number = /^source-([1-3])$/.exec(limits.attachment_id);
  if (!number || Number(number[1]) === batch.source) return false;
  const txt = batch.files[Number(number[1]) - 1];
  const table = batch.files[batch.source - 1];
  return Boolean(txt && txt.name.toLowerCase().endsWith('.txt') && txt.name === limits.filename &&
    txt.size === limits.size_bytes && table && fileAttachmentMime(table.name) === measurements.mime_type &&
    table.name === measurements.filename && table.size === measurements.size_bytes);
}

export const ChatView: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [isHistoryOpen, setIsHistoryOpen] = useState<boolean>(false);
  const [activeSessionId, setActiveSessionId] = useState<string>('');
  const [sessionTitle, setSessionTitle] = useState<string>('New Conversation');
  const [sessionCount, setSessionCount] = useState<number>(0);

  const abortControllerRef = useRef<AbortController | null>(null);
  const selectionVersion = useRef(0);
  const creating = useRef<AbortController | null>(null);
  const restorePending = useRef(false);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [draftRevision, setDraftRevision] = useState(0);
  const [restoring, setRestoring] = useState(false);
  const [localNotice, setLocalNotice] = useState('');
  const [localAccepted, setLocalAccepted] = useState(false);
  const [localStopping, setLocalStopping] = useState(false);
  const pendingLocal = useRef<LocalSubmission | null>(null);
  const sendingLocal = useRef<LocalSubmission | null>(null);
  const historyRequest = useRef(0);
  const attachmentUrls = useRef<Set<string>>(new Set());
  const releaseAttachmentUrls = useCallback(() => {
    for (const url of attachmentUrls.current) URL.revokeObjectURL(url);
    attachmentUrls.current.clear();
  }, []);
  useEffect(() => releaseAttachmentUrls, [releaseAttachmentUrls]);
  const { user, refreshUser } = useAuth();
  const selectionKey = `sovereign-active-conversation:${user?.id || ''}`;
  const { selectedModel, selectedVisionModel, addToast, capabilities, runProfile, isCheckingHealth, refreshCapabilities } = useWorkbench();
  const local = (runProfile ?? capabilities?.run_profile) === 'local_dev';
  const lastLocalUser = local ? latestLocalUser(messages) : undefined;
  const localRunning = lastLocalUser?.localTurn?.state === 'running';
  const modelSelection = useRef({ selectedModel, selectedVisionModel, capabilities });
  const approvalsAllowed = permitsCapability(capabilities, 'approval_resume');
  const textDisabled = chatDisabled(capabilities, false, selectedModel);
  // Image requests also use the selected text model for the reasoning stage.
  const imageDisabled = chatDisabled(capabilities, true, selectedVisionModel) || textDisabled;
  const chatStatus = capabilityFor(capabilities, capabilities ? 'chat' : 'backend', !isCheckingHealth);
  const imageStatus = capabilityFor(capabilities, 'multimodal', !isCheckingHealth);
  const selectedModelMissing = capabilities?.capabilities?.ollama?.state === 'available' &&
    selectedModel !== capabilities.models.default_chat.id &&
    !modelInInventory(capabilities, selectedModel);

  // Conversation contents are owned and persisted by the server.
  const updateSessionCount = useCallback(() => {
    setHistoryRevision(value => value + 1);
    void fetchSessions().then(result => setSessionCount(result.count)).catch(() => {});
  }, []);
  const clearLocalObservation = useCallback(() => {
    historyRequest.current++;
    pendingLocal.current = null;
    sendingLocal.current = null;
    setLocalNotice('');
    setLocalAccepted(false);
    setLocalStopping(false);
  }, []);
  const clearConversation = useCallback(() => {
    selectionVersion.current++;
    restorePending.current = false;
    creating.current?.abort();
    creating.current = null;
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    clearLocalObservation();
    releaseAttachmentUrls();
    setDraftRevision(revision => revision + 1);
    setRestoring(false);
    setIsHistoryOpen(false);
    setActiveSessionId('');
    setSessionTitle('New Conversation');
    setMessages([]);
    sessionStorage.removeItem(selectionKey);
    setIsStreaming(false);
  }, [clearLocalObservation, releaseAttachmentUrls, selectionKey]);
  const handleSessionExpired = useCallback(() => {
    clearConversation();
    addToast('info', 'Your session has expired. Sign in again to continue.');
    void refreshUser();
  }, [clearConversation, addToast, refreshUser]);

  const refreshLocalHistory = useCallback(async (id: string, version = selectionVersion.current) => {
    const request = ++historyRequest.current;
    const isCurrent = () => selectionVersion.current === version && historyRequest.current === request;
    try {
      const session = await loadSession(id);
      if (!isCurrent()) return;
      pendingLocal.current = reconcileLocalSubmission(session.messages, pendingLocal.current);
      setMessages(session.messages);
      setSessionTitle(session.title);
      setLocalNotice(pendingLocal.current ? 'The server has not confirmed this submission. You can resend the same attempt.' : '');
    } catch (error) {
      if (!isCurrent()) return;
      const status = (error as {status?: number})?.status;
      if (status === 401) {
        handleSessionExpired();
      } else if (status === 404) {
        clearConversation();
        updateSessionCount();
        addToast('info', 'This conversation is no longer available.');
      } else {
        setLocalNotice('Conversation history is unavailable. Refresh it when the connection returns.');
        // A preview from a broken stream cannot be treated as a final message.
        setMessages(previous => previous.filter(message => !message.isStreaming));
      }
    }
  }, [addToast, clearConversation, handleSessionExpired, updateSessionCount]);

  const runLocalSubmission = useCallback(async (command: LocalSubmission) => {
    if (abortControllerRef.current) return;
    const version = selectionVersion.current;
    const controller = new AbortController();
    const previewId = `preview-${command.turn_id}`;
    const current = () => version === selectionVersion.current && abortControllerRef.current === controller;
    historyRequest.current++;
    abortControllerRef.current = controller;
    sendingLocal.current = command;
    pendingLocal.current = command;
    setLocalNotice('');
    setLocalAccepted(false);
    setIsStreaming(true);
    setMessages(previous => [
      ...previous.filter(message => message.id !== command.turn_id && message.id !== previewId),
      { id: command.turn_id, role: 'user', content: command.message, timestamp: new Date().toLocaleTimeString() },
      { id: previewId, role: 'assistant', content: '', timestamp: '', model_used: command.model,
        isStreaming: true, localPreview: true },
    ]);
    try {
      await streamChat({ ...command, tools_enabled: false }, guardChatCallbacks({
        onAccepted: () => setLocalAccepted(true),
        onDelta: (delta: string) => setMessages(previous => previous.map(message =>
          message.id === previewId ? { ...message, content: message.content + delta } : message)),
        onSources: () => {},
        onDone: () => {},
        onError: (_message: string, info?: StreamErrorInfo) => {
          if (info?.status === 401) handleSessionExpired();
          // Even an error can race a completed write. History decides the outcome.
        },
      }, current), controller.signal);
    } catch {
      // Network errors are reconciled by the same read-only history request.
    } finally {
      if (current()) {
        setLocalNotice('Checking server-owned conversation history…');
        await refreshLocalHistory(command.session_id, version);
        if (current()) {
          abortControllerRef.current = null;
          sendingLocal.current = null;
          setLocalAccepted(false);
          setIsStreaming(false);
          updateSessionCount();
        }
      }
    }
  }, [handleSessionExpired, refreshLocalHistory, updateSessionCount]);

  useEffect(() => {
    updateSessionCount();
    return () => {
      selectionVersion.current++;
      creating.current?.abort();
      creating.current = null;
      abortControllerRef.current?.abort();
    };
  }, [updateSessionCount]);
  useEffect(() => {
    const saved = sessionStorage.getItem(selectionKey);
    if (!saved || !user?.id) return;
    const version = selectionVersion.current;
    restorePending.current = true;
    setRestoring(true);
    void loadSession(saved).then(session => {
      if (selectionVersion.current !== version) return;
      setActiveSessionId(session.id);
      setSessionTitle(session.title);
      setMessages(session.messages);
    }).catch((error) => {
      if (selectionVersion.current !== version) return;
      const status = (error as {status?: number})?.status;
      if (status === 401 || status === 403 || status === 404) {
        sessionStorage.removeItem(selectionKey);
        addToast('info', 'The previous conversation is unavailable.');
      } else {
        setActiveSessionId(saved);
        setLocalNotice('Conversation history is unavailable. Refresh it when the connection returns.');
      }
    }).finally(() => { if (selectionVersion.current === version) { restorePending.current = false; setRestoring(false); } });
    return () => { selectionVersion.current++; };
  }, [selectionKey, user?.id]);
  useEffect(() => {
    if (!user?.id || restoring || restorePending.current) return;
    if (activeSessionId) sessionStorage.setItem(selectionKey, activeSessionId);
    else sessionStorage.removeItem(selectionKey);
  }, [activeSessionId, selectionKey, restoring, user?.id]);

  const persistSession = useCallback((_messages: ChatMessage[], _title?: string) => {
    // Server-side chat handling persists messages; browser history is quarantined.
  }, []);

  const handleApproveRef = useRef<(taskId: string) => Promise<void>>(async () => {});
  const handleRejectRef = useRef<(taskId: string) => Promise<void>>(async () => {});

  /**
   * Core message sender — handles text-only, multimodal, and agent planning paths.
   */
  const handleSendMessage = useCallback(
    async (text: string, image?: File, textFile?: File, lineRange?: TextFileLineRange, batch?: TextFileBatch) => {
      if (!text.trim() || isStreaming || abortControllerRef.current || restoring || creating.current ||
          (local && (localRunning || localNotice || pendingLocal.current || localStopping))) return;
      if (textFile && capabilities?.text_file_understanding !== true) {
        addToast('info', 'Text-file questions require the authenticated local chat-only profile.');
        return;
      }
      const textReady = !chatDisabled(capabilities, false, selectedModel);
      if (!textReady || (image && chatDisabled(capabilities, true, selectedVisionModel))) {
        addToast('info', describeCapability(textReady ? imageStatus : chatStatus));
        return;
      }

      // Natural language approval / rejection interception when an approval is pending
      const activePendingMsg = messages.slice().reverse().find(
        (m) => m.pendingApproval && m.pendingApproval.status === 'pending'
      );
      const pendingApproval = activePendingMsg?.pendingApproval;

      if (!image && !textFile && pendingApproval) {
        const cleanText = text.trim().toLowerCase().replace(/^[^\w]+|[^\w]+$/g, '');
        const isApproval = /^(yes(\s+approve)?|approve[d]?|proceed|continue|confirm)$/i.test(cleanText);
        const isRejection = /^(no|reject(ed)?|deny|denied|cancel(led)?)$/i.test(cleanText);

        if ((isApproval || isRejection) && !approvalsAllowed) {
          addToast('info', 'Task approval and resume are blocked while task authorization cannot be safely revalidated.');
          return;
        }

        if (isApproval) {
          const userTimestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          const userMsg: ChatMessage = {
            id: 'msg_' + Math.random().toString(36).substring(2, 9),
            role: 'user',
            content: text,
            timestamp: userTimestamp,
          };
          setMessages((prev) => [...prev, userMsg]);
          handleApproveRef.current(pendingApproval.task_id);
          return;
        } else if (isRejection) {
          const userTimestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          const userMsg: ChatMessage = {
            id: 'msg_' + Math.random().toString(36).substring(2, 9),
            role: 'user',
            content: text,
            timestamp: userTimestamp,
          };
          setMessages((prev) => [...prev, userMsg]);
          handleRejectRef.current(pendingApproval.task_id);
          return;
        }
      }

      let sessionId = activeSessionId;
      if (!sessionId) {
        const version = selectionVersion.current;
        const creation = new AbortController();
        creating.current = creation;
        setIsStreaming(true);
        try {
          const created = await createSession(creation.signal);
          if (creation.signal.aborted || selectionVersion.current !== version) return;
          sessionId = created.session_id;
          setActiveSessionId(sessionId);
          updateSessionCount();
          const current = modelSelection.current;
          if (current.selectedModel !== selectedModel ||
              chatDisabled(current.capabilities, false, selectedModel) ||
              (image && (current.selectedVisionModel !== selectedVisionModel ||
                chatDisabled(current.capabilities, true, selectedVisionModel))) ||
              (textFile && current.capabilities?.text_file_understanding !== true)) {
            setIsStreaming(false);
            addToast('info', image || textFile ? 'Check the selected models and attach the file again.' :
              'Check the selected chat model and send your message again.');
            return;
          }
        } catch {
          if (!creation.signal.aborted && selectionVersion.current === version) {
            setIsStreaming(false);
            addToast('error', 'Conversation could not be created. Please try again.');
          }
          return;
        } finally {
          if (creating.current === creation) creating.current = null;
        }
      }
      if (local) {
        await runLocalSubmission({
          session_id: sessionId, turn_id: crypto.randomUUID(), attempt: 1, message: text, model: selectedModel,
        });
        return;
      }
      const userTimestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

      let userAttachments: ImageAttachment[] | undefined;
      if (image) {
        const objectUrl = URL.createObjectURL(image);
        attachmentUrls.current.add(objectUrl);
        userAttachments = [
          {
            id: `local_${Math.random().toString(36).substring(2, 9)}`,
            filename: image.name,
            mimeType: image.type,
            sizeBytes: image.size,
            objectUrl,
          },
        ];
      } else if (textFile) {
        userAttachments = (batch?.files ?? [textFile]).map((file, index) => ({
          id: `local_${Math.random().toString(36).slice(2)}`,
          filename: file.name, mimeType: fileAttachmentMime(file.name),
          sizeBytes: file.size, sourceNumber: batch ? index + 1 : undefined,
        }));
      }

      const userMsg: ChatMessage = {
        id: 'msg_' + Math.random().toString(36).substring(2, 9),
        role: 'user',
        content: text,
        timestamp: userTimestamp,
        attachments: userAttachments,
      };

      const assistantId = 'asst_' + Math.random().toString(36).substring(2, 9);
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: 'assistant',
        content: '',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        model_used: textFile ? undefined : selectedModel,
        sources: [],
        toolEvents: [],
        isStreaming: true,
        imageProgress: image ? 'uploading' : undefined,
        textFileAnswer: Boolean(textFile),
        fileName: textFile?.name,
      };

      // Determine smart title from first user query
      let currentTitle = sessionTitle;
      if (sessionTitle === 'New Conversation' && messages.length === 0 && !textFile) {
        currentTitle = text.slice(0, 38).trim() + (text.length > 38 ? '…' : '');
        setSessionTitle(currentTitle);
      }

      const updatedWithUser = [...messages, userMsg, assistantMsg];
      setMessages(updatedWithUser);
      setIsStreaming(true);

      // Persist snapshot with user message
      persistSession(updatedWithUser, currentTitle);

      const controller = new AbortController();
      abortControllerRef.current = controller;
      let taskCompletionReported = false;
      let batchAnswer = '';
      let comparisonPlanned = false;

      const callbacks = {
        onDelta: (delta: string) => {
          if (batch) {
            batchAnswer += delta;
            return;
          }
          setMessages((prev) => {
            const next = prev.map((msg) =>
              msg.id === assistantId
                ? { ...msg, content: msg.content + delta, isStreaming: true }
                : msg
            );
            return next;
          });
        },
        onSources: (sources: SourceReference[]) => {
          if (textFile) return;
          setMessages((prev) =>
            prev.map((msg) => (msg.id === assistantId ? { ...msg, sources } : msg))
          );
        },
        onToolEvent: (event: ToolEvent) => {
          if (textFile) return;
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? { ...msg, toolEvents: [...(msg.toolEvents || []), event] }
                : msg
            )
          );
        },
        onAgentStatus: (status: string) => {
          if (status === 'planning' || status === 'executing') {
            setMessages(prev => prev.map(msg => msg.id === assistantId
              ? { ...msg, workflowStatus: status } : msg));
          }
          if (image && (status === 'analyzing_image' || status === 'reasoning')) {
            setMessages(prev => prev.map(msg => msg.id === assistantId
              ? { ...msg, imageProgress: status } : msg));
          }
        },
        onPlanCreated: (planData: StreamChunkPayload) => {
          if (textFile && (planData.task_id || !Array.isArray(planData.plan?.steps) || !planData.plan.steps.length)) return;
          if (batch && (planData.plan?.objective === 'Compare TXT limits with CSV readings' ||
                        planData.plan?.objective === 'Compare TXT limits with JSON readings')) {
            comparisonPlanned = true;
          }
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? {
                    ...msg,
                    workflowStatus: 'executing',
                    plan: {
                      taskId: planData.task_id,
                      objective: planData.plan?.objective || '',
                      steps: planData.plan?.steps || [],
                    },
                  }
                : msg
            )
          );
        },
        onPlanStep: (stepData: StreamChunkPayload) => {
          setMessages((prev) =>
            prev.map((msg) => {
              if (msg.id !== assistantId || !msg.plan) return msg;
              const updatedSteps = msg.plan.steps.map((s) => {
                if (s.id === stepData.step_id) {
                  return {
                    ...s,
                    status: stepData.status || s.status,
                    description: stepData.description || s.description,
                    summary: stepData.status === 'completed' ? stepData.summary ?? s.summary : s.summary,
                  };
                }
                return s;
              });
              return {
                ...msg,
                plan: {
                  ...msg.plan,
                  steps: updatedSteps,
                },
              };
            })
          );
        },
        onApprovalRequired: (approvalData: any) => {
          if (textFile) return;
          setMessages((prev) => {
            const next = prev.map((msg) =>
              msg.id === assistantId
                ? {
                    ...msg,
                    isStreaming: true,
                    workflowStatus: 'awaiting_approval' as const,
                    pendingApproval: {
                      approval_id: approvalData.approval_id,
                      task_id: approvalData.task_id,
                      step_id: approvalData.step_id,
                      tool_name: approvalData.tool_name,
                      arguments_hash: approvalData.arguments_hash || '',
                      risk_level: approvalData.risk_level || 'high',
                      reason: approvalData.reason || '',
                      status: 'pending',
                      created_at: new Date().toISOString(),
                      expires_at: approvalData.expires_at || '',
                      ...((approvalData.arguments && { arguments: approvalData.arguments }) as any),
                    },
                  }
                : msg
            );
            persistSession(next, currentTitle);
            return next;
          });
          addToast('info', `Human approval required for tool: ${approvalData.tool_name}`);
        },
        onApprovalResolved: (_status: string) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? {
                    ...msg,
                    pendingApproval: undefined,
                  }
                : msg
            )
          );
        },
        onTaskStatus: (status: string, _taskId: string) => {
          if (status === 'task_completed') {
            taskCompletionReported = true;
            return;
          }
          const states = { task_started: 'planning',
            task_failed: 'failed', task_cancelled: 'cancelled' } as const;
          if (status === 'task_failed' || status === 'task_cancelled') taskCompletionReported = false;
          const nextStatus = states[status as keyof typeof states];
          if (nextStatus) setMessages(prev => prev.map(msg => msg.id === assistantId
            ? { ...msg, workflowStatus: nextStatus,
                ...((status === 'task_failed' || status === 'task_cancelled') ? { isStreaming: false } : {}) } : msg));
          if (status === 'task_failed' || status === 'task_cancelled') {
            setIsStreaming(false);
            updateSessionCount();
          }
          // Successful task events can precede the final text and done event.
        },
        onDone: (returnedSessionId: string, modelUsed: string, attachment?: StreamChunkPayload['attachment'],
          fileSources?: StreamChunkPayload['file_sources']) => {
          if (batch) {
            const selected = batch.files[batch.source - 1];
            if (!batchAnswer.trim() || returnedSessionId !== sessionId || attachment?.attachment_id !== 'source-' + batch.source ||
                attachment.filename !== selected.name || attachment.size_bytes !== selected.size ||
                attachment.mime_type !== fileAttachmentMime(selected.name) ||
                (comparisonPlanned && fileSources === undefined) ||
                (fileSources !== undefined && (modelUsed !== 'Deterministic file analysis' ||
                  !matchesComparisonSources(batch, fileSources)))) {
              callbacks.onError('', { code: 'TEXT_SOURCE_UNVERIFIED' });
              return;
            }
          }
          setMessages((prev) => {
            const next = prev.map((msg) =>
              msg.id === assistantId
                ? { ...msg, isStreaming: false, model_used: modelUsed || (textFile ? undefined : selectedModel),
                    ...(batch ? { content: batchAnswer, fileSource: batch.source, fileSources } : {}),
                    ...(taskCompletionReported && !msg.pendingApproval ? { workflowStatus: 'completed' as const } : {}) }
                : msg
            );
            persistSession(next, currentTitle);
            return next;
          });
          setIsStreaming(false);
          updateSessionCount();
        },
        onError: (errorText: string, info?: StreamErrorInfo) => {
          if (info?.status === 401 || info?.code === 'TEXT_FILE_AUTH_CHANGED') {
            handleSessionExpired();
            return;
          }
          if (errorText === 'Conversation unavailable' || errorText === 'Conversation not found' ||
              info?.code === 'CONVERSATION_UNAVAILABLE' || (textFile && info?.status === 404)) {
            clearConversation();
            updateSessionCount();
            addToast('error', 'This conversation is no longer available.');
            return;
          }
          const publicError = textFile || info?.code === 'TEXT_FILE_REATTACH_REQUIRED'
            ? fileTurnError(errorText, info) : errorText;
          setMessages((prev) => {
            const next = prev.map((msg) =>
              msg.id === assistantId
                ? { ...msg, errorMessage: publicError, error: true, isStreaming: false,
                    ...(info?.code === 'TEXT_SOURCE_UNVERIFIED' ? { plan: undefined } : {}),
                    ...(msg.plan?.steps.some(step => step.status === 'failed') && textFile
                      ? { workflowStatus: 'failed' as const }
                      : msg.workflowStatus === 'planning' || msg.workflowStatus === 'executing'
                        ? { workflowStatus: 'interrupted' as const } : {}) }
                : msg
            );
            persistSession(next, currentTitle);
            return next;
          });
          setIsStreaming(false);
          addToast('error', publicError);
        },
      };

      // Ignore late events from a cancelled or previously selected conversation.
      const guardedCallbacks = guardChatCallbacks(callbacks,
        () => abortControllerRef.current === controller && !controller.signal.aborted);
      try {
        if (batch) {
          await streamChatTextFiles(batch, sessionId, selectedModel, text, guardedCallbacks, controller.signal, lineRange);
        } else if (textFile) {
          await streamChatTextFile(textFile, sessionId, selectedModel, text, guardedCallbacks, controller.signal, lineRange);
        } else if (image) {
          await streamChatMultimodal(
            {
              session_id: sessionId,
              message: text,
              model: selectedModel,
              vision_model: selectedVisionModel,
              stream: true,
              image,
            },
            guardedCallbacks,
            controller.signal
          );
        } else {
          await streamChat(
            {
              session_id: sessionId,
              message: text,
              model: selectedModel,
              stream: true,
            },
            guardedCallbacks,
            controller.signal
          );
        }
      } catch (err: any) {
        if (abortControllerRef.current !== controller) return;
        if (err.name !== 'AbortError') guardedCallbacks.onError(err.message || 'Stream connection failed',
          { code: 'STREAM_CONNECTION_LOST' });
        setIsStreaming(false);
      } finally {
        if (abortControllerRef.current === controller) abortControllerRef.current = null;
      }
    },
    [activeSessionId, selectedModel, selectedVisionModel, capabilities, isStreaming, addToast, sessionTitle, messages, persistSession, updateSessionCount, approvalsAllowed, chatStatus, imageStatus, restoring, clearConversation, handleSessionExpired, local, localRunning, localNotice, localStopping, runLocalSubmission]
  );

  /**
   * Handle human approval of a pending task step
   */
  const handleApprove = async (taskId: string) => {
    if (!approvalsAllowed) return;
    try {
      addToast('info', 'Submitting approval...');
      let targetMessageId: string | null = null;
      setMessages((prev) =>
        prev.map((m) => {
          if (m.pendingApproval?.task_id === taskId || m.plan?.taskId === taskId) {
            targetMessageId = m.id;
            return { ...m, pendingApproval: undefined, isStreaming: true };
          }
          return m;
        })
      );
      setIsStreaming(true);

      const response = await approveTaskStep(taskId);
      if (!response.ok) {
        throw new Error(`Approval failed with status ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) return;

      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      const isTarget = (m: ChatMessage) =>
        (targetMessageId !== null && m.id === targetMessageId) || m.plan?.taskId === taskId;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(trimmed.slice(6));
            if (data.type === 'delta' && data.content) {
              setMessages((prev) =>
                prev.map((m) =>
                  isTarget(m)
                    ? { ...m, content: (m.content || '') + data.content, isStreaming: true }
                    : m
                )
              );
            } else if (data.type === 'plan_step') {
              setMessages((prev) =>
                prev.map((m) => {
                  if (!isTarget(m)) return m;
                  // If the message has a plan with matching step, update it
                  if (m.plan) {
                    const updatedSteps = m.plan.steps.map((s) =>
                      s.id === data.step_id ? { ...s, status: data.status } : s
                    );
                    return { ...m, plan: { ...m.plan, steps: updatedSteps } };
                  }
                  // Fallback: track step status in toolEvents so UI can reflect completion
                  return {
                    ...m,
                    toolEvents: [
                      ...(m.toolEvents || []),
                      {
                        type: 'plan_step' as any,
                        tool: data.tool_name || data.step_id,
                        status: data.status,
                      },
                    ],
                  };
                })
              );

            } else if (data.type === 'tool_start') {
              setMessages((prev) =>
                prev.map((m) =>
                  isTarget(m)
                    ? {
                        ...m,
                        toolEvents: [
                          ...(m.toolEvents || []),
                          {
                            type: 'tool_start',
                            tool: data.tool,
                            arguments: data.arguments,
                          },
                        ],
                      }
                    : m
                )
              );
            } else if (data.type === 'tool_result') {
              setMessages((prev) =>
                prev.map((m) =>
                  isTarget(m)
                    ? {
                        ...m,
                        toolEvents: [
                          ...(m.toolEvents || []),
                          {
                            type: 'tool_result',
                            tool: data.tool,
                            success: data.success,
                            summary: data.summary,
                          },
                        ],
                      }
                    : m
                )
              );
            } else if (data.type === 'task_completed' || data.type === 'done') {
              setMessages((prev) =>
                prev.map((m) =>
                  isTarget(m)
                    ? { ...m, isStreaming: false }
                    : m
                )
              );
            }
          } catch {}
        }
      }
      setIsStreaming(false);
      setMessages((prev) => {
        const next = prev.map((m) =>
          isTarget(m)
            ? {
                ...m,
                isStreaming: false,
                content: m.content || 'Approval response received.',
              }
            : m
        );
        persistSession(next);
        return next;
      });
      addToast('info', 'Approval response received.');
    } catch (err: any) {
      addToast('error', `Approval execution failed: ${err.message}`);
      setIsStreaming(false);
      setMessages((prev) => {
        const next = prev.map((m) =>
          m.plan?.taskId === taskId
            ? { ...m, isStreaming: false, error: true }
            : m
        );
        persistSession(next);
        return next;
      });
    }
  };

  /**
   * Handle human rejection
   */
  const handleReject = async (taskId: string) => {
    if (!approvalsAllowed) return;
    try {
      addToast('info', 'Rejecting step...');
      setMessages((prev) =>
        prev.map((m) =>
          m.pendingApproval?.task_id === taskId || m.plan?.taskId === taskId
            ? {
                ...m,
                pendingApproval: undefined,
                isStreaming: false,
                content: m.content || 'Task step rejected by operator. Execution halted.',
              }
            : m
        )
      );

      const response = await rejectTaskStep(taskId, 'Rejected by operator');
      if (!response.ok) {
        throw new Error(`Rejection failed with status ${response.status}`);
      }
      addToast('info', 'Task step rejected. Execution halted.');
    } catch (err: any) {
      addToast('error', `Rejection failed: ${err.message}`);
    }
  };

  handleApproveRef.current = handleApprove;
  handleRejectRef.current = handleReject;

  const stopLocalResponse = async () => {
    const live = sendingLocal.current;
    const attempt = live && localAccepted ? live :
      localRunning && lastLocalUser?.localTurn && {
        session_id: activeSessionId, turn_id: lastLocalUser.id, attempt: lastLocalUser.localTurn.attempt,
      };
    if (!attempt || localStopping) return;
    const version = selectionVersion.current;
    const observer = abortControllerRef.current;
    setLocalStopping(true);
    try {
      await cancelLocalTurn(attempt.session_id, attempt.turn_id, attempt.attempt);
      if (version !== selectionVersion.current) return;
      if (abortControllerRef.current === observer) {
        observer?.abort();
        abortControllerRef.current = null;
        sendingLocal.current = null;
        setLocalAccepted(false);
        setIsStreaming(false);
      }
    } catch (error) {
      if (version !== selectionVersion.current) return;
      if ((error as {status?: number})?.status === 401) {
        handleSessionExpired();
        return;
      }
      addToast('error', 'The server did not confirm cancellation.');
    } finally {
      if (version === selectionVersion.current) {
        if (!abortControllerRef.current) await refreshLocalHistory(attempt.session_id, version);
        if (version === selectionVersion.current) setLocalStopping(false);
      }
    }
  };

  const handleStopStream = () => {
    if (local) {
      void stopLocalResponse();
      return;
    }
    if (creating.current) {
      selectionVersion.current++;
      creating.current.abort();
      creating.current = null;
      setIsStreaming(false);
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
      setIsStreaming(false);
      setMessages((prev) => {
        const next = prev.map((msg) => (msg.isStreaming ? { ...msg, isStreaming: false, workflowStatus: 'interrupted' as const } : msg));
        persistSession(next);
        return next;
      });
      addToast('info', 'Stopped receiving the response. The operation may still be running.');
    }
  };

  const leaveConversation = () => {
    if (!local) {
      handleStopStream();
      return;
    }
    creating.current?.abort();
    creating.current = null;
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    clearLocalObservation();
    setIsStreaming(false);
  };

  // Start a fresh, clean conversation session
  const handleStartNewChat = () => {
    leaveConversation();
    selectionVersion.current++;
    releaseAttachmentUrls();
    restorePending.current = false;
    setRestoring(false);
    setDraftRevision(revision => revision + 1);
    setActiveSessionId('');
    setSessionTitle('New Conversation');
    setMessages([]);
    addToast('info', 'Started new conversation session.');
  };

  // Switch to a previous conversation session from history
  const handleSelectSession = (session: ChatSession) => {
    leaveConversation();
    selectionVersion.current++;
    releaseAttachmentUrls();
    restorePending.current = false;
    setRestoring(false);
    setDraftRevision(revision => revision + 1);
    setActiveSessionId(session.id);
    setSessionTitle(session.title);
    setMessages(session.messages);
    addToast('info', `Resumed session: ${session.title}`);
  };

  useEffect(() => {
    modelSelection.current = { selectedModel, selectedVisionModel, capabilities };
  }, [selectedModel, selectedVisionModel, capabilities]);

  // Listen to Demo Scenario triggers
  useEffect(() => {
    const lifetime = new AbortController();
    const handlePreloadDemo = async (e: Event) => {
      const customEvent = e as CustomEvent<{
        prompt: string;
        imageFile?: string;
        isMultimodal: boolean;
      }>;
      const { prompt, imageFile, isMultimodal } = customEvent.detail;
      if (!prompt) return;

      if (isMultimodal) {
        const version = selectionVersion.current;
        const canSend = () => !lifetime.signal.aborted && selectionVersion.current === version;
        try {
          if (imageFile && !imageDisabled) {
            const imgRes = await fetch(`/api/documents/view/images/${encodeURIComponent(imageFile)}`,
                                       { signal: lifetime.signal });
            if (imgRes.ok) {
              const blob = await imgRes.blob();
              if (canSend()) {
                const file = new File([blob], imageFile, { type: blob.type });
                void handleSendMessage(prompt, file);
              }
              return;
            }
          }
        } catch {
          // Attachment retrieval can fail or be cancelled while leaving the prompt unsent.
        }
        if (canSend()) addToast('info', 'The demo image is unavailable. Check image readiness and attach an image to continue.');
        return;
      }

      handleSendMessage(prompt);
    };

    window.addEventListener('workbench:preload-demo', handlePreloadDemo);
    return () => {
      lifetime.abort();
      window.removeEventListener('workbench:preload-demo', handlePreloadDemo);
    };
  }, [handleSendMessage, imageDisabled, addToast]);

  useEffect(() => {
    if (!local || !activeSessionId || isStreaming || localStopping) return;
    let checking = false;
    const version = selectionVersion.current;
    const refresh = () => {
      if (checking || abortControllerRef.current || selectionVersion.current !== version) return;
      checking = true;
      void refreshLocalHistory(activeSessionId, version).finally(() => { checking = false; });
    };
    window.addEventListener('online', refresh);
    const timer = localRunning ? window.setInterval(refresh, 1500) : undefined;
    return () => {
      window.removeEventListener('online', refresh);
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [local, activeSessionId, isStreaming, localStopping, localRunning, refreshLocalHistory]);

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden bg-white relative font-sans">
      {/* 1. Main Workspace Top Header */}
      <div className="h-13 border-b border-slate-200 px-5 flex items-center justify-between shrink-0 bg-white/95 backdrop-blur-md z-20 shadow-sm">
        <div className="flex items-center gap-3">
          {/* History Toggle Button */}
          <button
            onClick={() => setIsHistoryOpen(!isHistoryOpen)}
            className={`px-3 py-1.5 rounded-lg border text-xs font-medium flex items-center gap-2 transition-all ${
              isHistoryOpen
                ? 'bg-blue-50 text-blue-700 border-blue-200 shadow-sm'
                : 'bg-white text-slate-600 border-slate-200 hover:border-blue-300 hover:text-blue-600 shadow-sm'
            }`}
            title="Open Chat History"
          >
            <Clock className="w-4 h-4 text-blue-500" />
            <span>History</span>
            {sessionCount > 0 && (
              <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-slate-100 text-slate-600 border border-slate-200">
                {sessionCount}
              </span>
            )}
          </button>

          {/* New Chat Button */}
          <button
            onClick={handleStartNewChat}
            className="px-3 py-1.5 rounded-lg bg-white border border-slate-200 hover:border-blue-300 text-slate-600 hover:text-blue-600 text-xs font-medium flex items-center gap-1.5 transition-all shadow-sm"
            title="Start New Conversation"
          >
            <Plus className="w-4 h-4 text-blue-500" />
            <span>New Chat</span>
          </button>

          {/* Current Session Title */}
          <div className="hidden sm:flex items-center gap-2 pl-3 border-l border-slate-200">
            <span className="text-sm font-semibold text-slate-800 truncate max-w-[280px]">
              {sessionTitle}
            </span>
            <span className="text-xs text-slate-400">
              {activeSessionId ? `(${activeSessionId.substring(0, 8)}...)` : ''}
            </span>
          </div>
        </div>

        {/* Right Status */}
        <div className="flex items-center gap-3 text-xs">
          {local && <button type="button" disabled={isCheckingHealth} onClick={() => void refreshCapabilities(true)}
            className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-slate-700 disabled:opacity-50">
            Check Ollama
          </button>}
          <span className="text-xs text-slate-500 hidden md:inline">
            Model: <strong className="text-blue-600">{selectedModel}</strong>
          </span>

        </div>
      </div>

      {/* 2. Persistent Chat History Drawer */}
      {isHistoryOpen && <ChatHistorySidebar
        isOpen={isHistoryOpen}
        onClose={() => setIsHistoryOpen(false)}
        currentSessionId={activeSessionId}
        onSelectSession={handleSelectSession}
        onNewChat={handleStartNewChat}
        onDeleted={updateSessionCount}
        refreshKey={historyRevision}
        onRenamed={(id, title) => { if (id === activeSessionId) setSessionTitle(title); }}
      />}

      {/* 3. Message List with Visual Explanations & Mermaid Renderers */}
      <MessageList
        messages={messages}
        onSelectPrompt={handleSendMessage}
        onApprove={approvalsAllowed ? handleApprove : undefined}
        onReject={approvalsAllowed ? handleReject : undefined}
      />

      {!approvalsAllowed && messages.some(message => message.pendingApproval?.status === 'pending') &&
        <p className="px-6 py-2 text-xs text-slate-700" role="status">
          {describeCapability(capabilityFor(capabilities, 'approval_resume', !isCheckingHealth))}
        </p>}
      {local && activeSessionId && (localNotice || localStopping || localRunning ||
        (!isStreaming && retryLocalSubmission(activeSessionId, lastLocalUser))) && (
        <div className="flex flex-wrap items-center gap-3 border-t border-slate-200 px-5 py-2 text-xs text-slate-700">
          <span role="status">{localStopping ? 'Stopping response…' : localNotice ||
            (localRunning && !isStreaming ? 'Reconnecting to conversation history…' : '')}</span>
          {!isStreaming && !localStopping && <button type="button" className="underline"
            onClick={() => void refreshLocalHistory(activeSessionId)}>Refresh conversation</button>}
          {!isStreaming && !localStopping && !localRunning && (pendingLocal.current ||
            (!localNotice && retryLocalSubmission(activeSessionId, lastLocalUser))) && (
            <button type="button" className="underline" onClick={() => {
              const command = pendingLocal.current || retryLocalSubmission(activeSessionId, lastLocalUser);
              if (command) void runLocalSubmission(command);
            }}>{pendingLocal.current ? 'Resend same attempt' : 'Retry response'}</button>
          )}
        </div>
      )}
      {/* 4. Input Composer */}
      <ChatInput
        key={activeSessionId ? activeSessionId + ':' + draftRevision : 'new-draft-' + draftRevision}
        onSendMessage={handleSendMessage}
        sendBlocked={textDisabled || restoring || (local && Boolean(localNotice || localStopping))}
        imageBlocked={imageDisabled || restoring}
        textOnly={local}
        hasEarlierImage={messages.some(message => message.attachments?.some(file => file.mimeType.startsWith('image/')))}
        hasEarlierTextFile={messages.some(message => message.attachments?.some(file => file.mimeType === 'text/plain'))}
        hasEarlierCsvFile={messages.some(message => message.attachments?.some(file => file.mimeType === 'text/csv'))}
        hasEarlierJsonFile={messages.some(message => message.attachments?.some(file => file.mimeType === 'application/json'))}
        onSendTextFile={capabilities?.text_file_understanding === true
          ? (question, file, range) => handleSendMessage(question, undefined, file, range) : undefined}
        onSendTextFiles={capabilities?.text_file_understanding === true
          ? (question, batch, range) => {
            const file = batch.files[batch.source - 1];
            if (file) return handleSendMessage(question, undefined, file, range, batch);
          } : undefined}
        textFileSupported={capabilities?.text_file_understanding === true}
        onSessionExpired={handleSessionExpired}
        sessionId={activeSessionId || undefined}
        sendStatus={selectedModelMissing && chatStatus.state !== 'blocked' && chatStatus.state !== 'unavailable'
          ? 'The selected model was not found in the checked inventory. Select an available model.'
          : textDisabled && chatStatus.state === 'available' ? 'Verify the selected text model in Models before sending.'
            : textDisabled || chatStatus.state === 'unknown' ? describeCapability(chatStatus) : undefined}
        imageStatus={imageDisabled ? (textDisabled ? 'The selected chat model is unavailable. Choose an available model before sending an image.' : describeCapability(imageStatus)) : undefined}
        onStopStream={handleStopStream}
        stopDisabled={local && (localStopping || (!localAccepted && !localRunning))}
        isStreaming={isStreaming || localRunning}
        onClearSession={handleStartNewChat}
      />
    </div>
  );
};
