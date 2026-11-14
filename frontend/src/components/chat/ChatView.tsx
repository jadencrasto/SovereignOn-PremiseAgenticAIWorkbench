/**
 * frontend/src/components/chat/ChatView.tsx
 * ------------------------------------------
 * Main Chat & Agent Conversation Interface with Persistent Multi-Session History,
 * Visual Explanations (Mermaid, Callouts, Tables), and Resumable Workflows.
 */

import React, { useState, useRef, useCallback, useEffect } from 'react';
import type { ChatMessage, ImageAttachment, SourceReference, ToolEvent } from '../../types';
import { MessageList } from './MessageList';
import { ChatInput } from './ChatInput';
import { useWorkbench } from '../../context/WorkbenchContext';
import { createSession, fetchSessions, streamChat, streamChatMultimodal, guardChatCallbacks } from '../../api/chat';
import { useAuth } from '../../context/AuthContext';
import { capabilityFor, chatDisabled, describeCapability, permitsCapability, modelInInventory } from '../../api/capabilities';
import { approveTaskStep, rejectTaskStep } from '../../api/tasks';
import {
  ChatHistorySidebar, loadSession,
  type ChatSession,
} from './ChatHistorySidebar';
import {
  Clock,
  Plus,
} from 'lucide-react';

export const ChatView: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [isHistoryOpen, setIsHistoryOpen] = useState<boolean>(false);
  const [activeSessionId, setActiveSessionId] = useState<string>('');
  const [sessionTitle, setSessionTitle] = useState<string>('New Conversation');
  const [sessionCount, setSessionCount] = useState<number>(0);

  const abortControllerRef = useRef<AbortController | null>(null);
  const selectionVersion = useRef(0);
  const creating = useRef(false);
  const restorePending = useRef(false);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [restoring, setRestoring] = useState(false);
  const attachmentUrls = useRef<Set<string>>(new Set());
  useEffect(() => {
    const current = new Set(messages.flatMap(message =>
      (message.attachments || []).map(attachment => attachment.objectUrl).filter((url): url is string => !!url)));
    for (const url of attachmentUrls.current) if (!current.has(url)) URL.revokeObjectURL(url);
    attachmentUrls.current = current;
  }, [messages]);
  useEffect(() => () => {
    for (const url of attachmentUrls.current) URL.revokeObjectURL(url);
    attachmentUrls.current.clear();
  }, []);
  const { user } = useAuth();
  const selectionKey = `sovereign-active-conversation:${user?.id || ''}`;
  const { selectedModel, addToast, capabilities, isCheckingHealth } = useWorkbench();
  const approvalsAllowed = permitsCapability(capabilities, 'approval_resume');
  const textDisabled = chatDisabled(capabilities, false, selectedModel);
  // Image requests also use the selected text model for the reasoning stage.
  const imageDisabled = chatDisabled(capabilities, true) || textDisabled;
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
  useEffect(() => {
    updateSessionCount();
    return () => { abortControllerRef.current?.abort(); };
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
    }).catch(() => {
      if (selectionVersion.current !== version) return;
      sessionStorage.removeItem(selectionKey);
      addToast('info', 'The previous conversation is unavailable.');
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
    async (text: string, image?: File) => {
      if (!text.trim() || isStreaming || restoring || creating.current) return;
      if (image ? imageDisabled : textDisabled) {
        addToast('info', describeCapability(image ? imageStatus : chatStatus));
        return;
      }

      // Natural language approval / rejection interception when an approval is pending
      const activePendingMsg = messages.slice().reverse().find(
        (m) => m.pendingApproval && m.pendingApproval.status === 'pending'
      );
      const pendingApproval = activePendingMsg?.pendingApproval;

      if (pendingApproval) {
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
        creating.current = true;
        setIsStreaming(true);
        try {
          const created = await createSession();
          if (selectionVersion.current !== version) return;
          sessionId = created.session_id;
          setActiveSessionId(sessionId);
          updateSessionCount();
        } catch {
          if (selectionVersion.current === version) {
            setIsStreaming(false);
            addToast('error', 'Conversation could not be created. Please try again.');
          }
          return;
        } finally {
          creating.current = false;
        }
      }
      const userTimestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

      let userAttachments: ImageAttachment[] | undefined;
      if (image) {
        userAttachments = [
          {
            id: `local_${Math.random().toString(36).substring(2, 9)}`,
            filename: image.name,
            mimeType: image.type,
            sizeBytes: image.size,
            objectUrl: URL.createObjectURL(image),
          },
        ];
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
        model_used: selectedModel,
        sources: [],
        toolEvents: [],
        isStreaming: true,
      };

      // Determine smart title from first user query
      let currentTitle = sessionTitle;
      if (sessionTitle === 'New Conversation' && messages.length === 0) {
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

      const callbacks = {
        onDelta: (delta: string) => {
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
          setMessages((prev) =>
            prev.map((msg) => (msg.id === assistantId ? { ...msg, sources } : msg))
          );
        },
        onToolEvent: (event: ToolEvent) => {
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
        },
        onPlanCreated: (planData: any) => {
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
        onPlanStep: (stepData: any) => {
          setMessages((prev) =>
            prev.map((msg) => {
              if (msg.id !== assistantId || !msg.plan) return msg;
              const updatedSteps = msg.plan.steps.map((s) => {
                if (s.id === stepData.step_id) {
                  return {
                    ...s,
                    status: stepData.status || s.status,
                    description: stepData.description || s.description,
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
            ? { ...msg, workflowStatus: nextStatus } : msg));
          // Final text may follow the task event; wait for the SSE terminal event.
        },
        onDone: (_returnedSessionId: string, modelUsed: string) => {
          setMessages((prev) => {
            const next = prev.map((msg) =>
              msg.id === assistantId
                ? { ...msg, isStreaming: false, model_used: modelUsed || selectedModel,
                    ...(taskCompletionReported && !msg.pendingApproval ? { workflowStatus: 'completed' as const } : {}) }
                : msg
            );
            persistSession(next, currentTitle);
            return next;
          });
          setIsStreaming(false);
          updateSessionCount();
        },
        onError: (errorText: string) => {
          if (errorText === 'Conversation unavailable' || errorText === 'Conversation not found') {
            selectionVersion.current++;
            setActiveSessionId('');
            setSessionTitle('New Conversation');
            setMessages([]);
            sessionStorage.removeItem(selectionKey);
            setIsStreaming(false);
            updateSessionCount();
            addToast('error', 'This conversation is no longer available.');
            return;
          }
          setMessages((prev) => {
            const next = prev.map((msg) =>
              msg.id === assistantId
                ? { ...msg, content: errorText, error: true, isStreaming: false,
                    ...(msg.workflowStatus === 'planning' || msg.workflowStatus === 'executing'
                      ? { workflowStatus: 'interrupted' as const } : {}) }
                : msg
            );
            persistSession(next, currentTitle);
            return next;
          });
          setIsStreaming(false);
          addToast('error', `Chat error: ${errorText}`);
        },
      };

      // Ignore late events from a cancelled or previously selected conversation.
      const guardedCallbacks = guardChatCallbacks(callbacks,
        () => abortControllerRef.current === controller && !controller.signal.aborted);
      try {
        if (image) {
          await streamChatMultimodal(
            {
              session_id: sessionId,
              message: text,
              model: selectedModel,
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
        if (err.name !== 'AbortError') {
          setMessages((prev) => {
            const next = prev.map((msg) =>
              msg.id === assistantId
                ? {
                    ...msg,
                    content: err.message || 'Stream connection failed',
                    error: true,
                    isStreaming: false,
                  }
                : msg
            );
            persistSession(next, currentTitle);
            return next;
          });
        }
        setIsStreaming(false);
      } finally {
        if (abortControllerRef.current === controller) abortControllerRef.current = null;
      }
    },
    [activeSessionId, selectedModel, isStreaming, addToast, sessionTitle, messages, persistSession, updateSessionCount, approvalsAllowed, textDisabled, imageDisabled, chatStatus, imageStatus, restoring, selectionKey]
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

  const handleStopStream = () => {
    if (creating.current) {
      selectionVersion.current++;
      setIsStreaming(false);
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
      setIsStreaming(false);
      setMessages((prev) => {
        const next = prev.map((msg) => (msg.isStreaming ? { ...msg, isStreaming: false, ...(msg.workflowStatus === 'planning' || msg.workflowStatus === 'executing' ? { workflowStatus: 'interrupted' as const } : {}) } : msg));
        persistSession(next);
        return next;
      });
      addToast('info', 'Generation interrupted.');
    }
  };

  // Start a fresh, clean conversation session
  const handleStartNewChat = () => {
    handleStopStream();
    selectionVersion.current++;
    restorePending.current = false;
    setRestoring(false);
    setActiveSessionId('');
    setSessionTitle('New Conversation');
    setMessages([]);
    addToast('info', 'Started new conversation session.');
  };

  // Switch to a previous conversation session from history
  const handleSelectSession = (session: ChatSession) => {
    handleStopStream();
    selectionVersion.current++;
    restorePending.current = false;
    setRestoring(false);
    setActiveSessionId(session.id);
    setSessionTitle(session.title);
    setMessages(session.messages);
    addToast('info', `Resumed session: ${session.title}`);
  };

  const handleRetry = () => {
    const lastUserMessage = [...messages].reverse().find((m) => m.role === 'user');
    if (lastUserMessage) {
      handleSendMessage(lastUserMessage.content);
    }
  };

  // Listen to Demo Scenario triggers
  useEffect(() => {
    const handlePreloadDemo = async (e: Event) => {
      const customEvent = e as CustomEvent<{
        prompt: string;
        imageFile?: string;
        isMultimodal: boolean;
      }>;
      const { prompt, imageFile, isMultimodal } = customEvent.detail;
      if (!prompt) return;

      if (isMultimodal && imageFile) {
        try {
          const imgRes = await fetch(`/api/documents/view/images/${imageFile}`);
          if (imgRes.ok) {
            const blob = await imgRes.blob();
            const file = new File([blob], imageFile, { type: blob.type || 'image/png' });
            handleSendMessage(prompt, file);
            return;
          }
        } catch (err) {
          console.warn('Could not auto-fetch demo image blob:', err);
        }
      }

      handleSendMessage(prompt);
    };

    window.addEventListener('workbench:preload-demo', handlePreloadDemo);
    return () => window.removeEventListener('workbench:preload-demo', handlePreloadDemo);
  }, [handleSendMessage]);

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
        onRetry={handleRetry}
        onApprove={approvalsAllowed ? handleApprove : undefined}
        onReject={approvalsAllowed ? handleReject : undefined}
      />

      {!approvalsAllowed && messages.some(message => message.pendingApproval?.status === 'pending') &&
        <p className="px-6 py-2 text-xs text-slate-700" role="status">
          {describeCapability(capabilityFor(capabilities, 'approval_resume', !isCheckingHealth))}
        </p>}
      {/* 4. Input Composer */}
      <ChatInput
        key={activeSessionId || "new-draft"}
        onSendMessage={handleSendMessage}
        sendBlocked={textDisabled || restoring}
        imageBlocked={imageDisabled}
        sendStatus={selectedModelMissing && chatStatus.state !== 'blocked' && chatStatus.state !== 'unavailable'
          ? 'The selected model was not found in the checked inventory. Select an available model.'
          : textDisabled || chatStatus.state === 'unknown' ? describeCapability(chatStatus) : undefined}
        imageStatus={imageDisabled ? (textDisabled ? 'The selected chat model is unavailable. Choose an available model before sending an image.' : describeCapability(imageStatus)) : undefined}
        onStopStream={handleStopStream}
        isStreaming={isStreaming}
        onClearSession={handleStartNewChat}
      />
    </div>
  );
};
