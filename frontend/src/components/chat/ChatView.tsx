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
import { streamChat, streamChatMultimodal } from '../../api/chat';
import { approveTaskStep, rejectTaskStep } from '../../api/tasks';
import {
  ChatHistorySidebar,
  loadAllSessions,
  saveSessionToStorage,
  type ChatSession,
} from './ChatHistorySidebar';
import {
  Clock,
  Plus,
  RotateCcw,
  Sparkles,
  MessageSquare,
  ChevronRight,
  HelpCircle,
  FileText,
} from 'lucide-react';

export const ChatView: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [isHistoryOpen, setIsHistoryOpen] = useState<boolean>(false);
  const [activeSessionId, setActiveSessionId] = useState<string>(() => {
    return 'sess_' + Math.random().toString(36).substring(2, 10);
  });
  const [sessionTitle, setSessionTitle] = useState<string>('New Conversation');
  const [sessionCount, setSessionCount] = useState<number>(0);

  const abortControllerRef = useRef<AbortController | null>(null);
  const { sessionId: contextSessionId, selectedModel, addToast } = useWorkbench();

  // Load last active session on initial mount if available
  useEffect(() => {
    const all = loadAllSessions();
    setSessionCount(all.length);
    if (all.length > 0 && messages.length === 0) {
      const latest = all[0];
      setActiveSessionId(latest.id);
      setSessionTitle(latest.title);
      setMessages(latest.messages);
    }
  }, []);

  // Sync session count whenever history is loaded
  const updateSessionCount = useCallback(() => {
    setSessionCount(loadAllSessions().length);
  }, []);

  // Helper to persist current conversation to storage
  const persistSession = useCallback(
    (newMessages: ChatMessage[], customTitle?: string) => {
      if (newMessages.length === 0) return;
      const titleToSave = customTitle || sessionTitle;
      const sessionObj: ChatSession = {
        id: activeSessionId,
        title: titleToSave,
        createdAt: Date.now(),
        updatedAt: Date.now(),
        messages: newMessages,
        model: selectedModel,
      };
      saveSessionToStorage(sessionObj);
      updateSessionCount();
    },
    [activeSessionId, sessionTitle, selectedModel, updateSessionCount]
  );

  /**
   * Core message sender — handles text-only, multimodal, and agent planning paths.
   */
  const handleSendMessage = useCallback(
    async (text: string, image?: File) => {
      if (!text.trim() || isStreaming) return;

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
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? {
                    ...msg,
                    toolEvents: [
                      ...(msg.toolEvents || []),
                      {
                        type: 'tool_start' as const,
                        tool: status === 'analyzing_image'
                          ? '🔍 Vision Analysis'
                          : status === 'reasoning'
                          ? '🧠 Reasoning & Diagramming'
                          : `⚙ ${status}`,
                        arguments: {},
                      },
                    ],
                  }
                : msg
            )
          );
        },
        onPlanCreated: (planData: any) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? {
                    ...msg,
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
                    isStreaming: false,
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
          setIsStreaming(false);
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
          if (status === 'task_completed' || status === 'task_failed' || status === 'task_cancelled') {
            setMessages((prev) => {
              const next = prev.map((msg) =>
                msg.id === assistantId ? { ...msg, isStreaming: false } : msg
              );
              persistSession(next, currentTitle);
              return next;
            });
            setIsStreaming(false);
          }
        },
        onDone: (_returnedSessionId: string, modelUsed: string) => {
          setMessages((prev) => {
            const next = prev.map((msg) =>
              msg.id === assistantId
                ? { ...msg, isStreaming: false, model_used: modelUsed || selectedModel }
                : msg
            );
            persistSession(next, currentTitle);
            return next;
          });
          setIsStreaming(false);
        },
        onError: (errorText: string) => {
          setMessages((prev) => {
            const next = prev.map((msg) =>
              msg.id === assistantId
                ? { ...msg, content: errorText, error: true, isStreaming: false }
                : msg
            );
            persistSession(next, currentTitle);
            return next;
          });
          setIsStreaming(false);
          addToast('error', `Chat error: ${errorText}`);
        },
      };

      try {
        if (image) {
          await streamChatMultimodal(
            {
              session_id: activeSessionId,
              message: text,
              model: selectedModel,
              stream: true,
              image,
            },
            callbacks,
            controller.signal
          );
        } else {
          await streamChat(
            {
              session_id: activeSessionId,
              message: text,
              model: selectedModel,
              stream: true,
            },
            callbacks,
            controller.signal
          );
        }
      } catch (err: any) {
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
        abortControllerRef.current = null;
      }
    },
    [activeSessionId, selectedModel, isStreaming, addToast, sessionTitle, messages, persistSession]
  );

  /**
   * Handle human approval of a pending task step
   */
  const handleApprove = async (taskId: string) => {
    try {
      addToast('info', 'Submitting approval...');
      setMessages((prev) =>
        prev.map((m) =>
          m.pendingApproval?.task_id === taskId
            ? { ...m, pendingApproval: undefined, isStreaming: true }
            : m
        )
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
                  m.plan?.taskId === taskId
                    ? { ...m, content: m.content + data.content }
                    : m
                )
              );
            } else if (data.type === 'plan_step') {
              setMessages((prev) =>
                prev.map((m) => {
                  if (m.plan?.taskId !== taskId) return m;
                  const updatedSteps = m.plan.steps.map((s) =>
                    s.id === data.step_id ? { ...s, status: data.status } : s
                  );
                  return { ...m, plan: { ...m.plan, steps: updatedSteps } };
                })
              );
            }
          } catch {}
        }
      }
      setIsStreaming(false);
      setMessages((prev) => {
        persistSession(prev);
        return prev;
      });
      addToast('success', 'Step approved and executed.');
    } catch (err: any) {
      addToast('error', `Approval execution failed: ${err.message}`);
      setIsStreaming(false);
    }
  };

  /**
   * Handle human rejection
   */
  const handleReject = async (taskId: string) => {
    try {
      addToast('info', 'Rejecting step...');
      setMessages((prev) =>
        prev.map((m) =>
          m.pendingApproval?.task_id === taskId
            ? { ...m, pendingApproval: undefined }
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

  const handleStopStream = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
      setIsStreaming(false);
      setMessages((prev) => {
        const next = prev.map((msg) => (msg.isStreaming ? { ...msg, isStreaming: false } : msg));
        persistSession(next);
        return next;
      });
      addToast('info', 'Generation interrupted.');
    }
  };

  // Start a fresh, clean conversation session
  const handleStartNewChat = () => {
    handleStopStream();
    const newId = 'sess_' + Math.random().toString(36).substring(2, 10);
    setActiveSessionId(newId);
    setSessionTitle('New Conversation');
    setMessages([]);
    addToast('info', 'Started new conversation session.');
  };

  // Switch to a previous conversation session from history
  const handleSelectSession = (session: ChatSession) => {
    handleStopStream();
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
    <div className="flex-1 flex flex-col h-full overflow-hidden bg-[#090d16] relative font-sans">
      {/* 1. Main Workspace Top Header */}
      <div className="h-13 border-b border-slate-800/90 px-5 flex items-center justify-between shrink-0 bg-[#0c1322]/95 backdrop-blur-md z-20 shadow-md">
        <div className="flex items-center gap-3">
          {/* History Toggle Button */}
          <button
            onClick={() => setIsHistoryOpen(!isHistoryOpen)}
            className={`px-2.5 py-1.5 rounded-lg border text-xs font-semibold flex items-center gap-2 transition-all ${
              isHistoryOpen
                ? 'bg-sky-600 text-white border-sky-500 shadow-md shadow-sky-600/30'
                : 'bg-slate-900 text-slate-300 border-slate-800 hover:border-slate-700 hover:text-white'
            }`}
            title="Open Chat History"
          >
            <Clock className="w-3.5 h-3.5 text-sky-400" />
            <span>History</span>
            {sessionCount > 0 && (
              <span className="text-[10px] font-mono px-1.5 py-0.2 rounded-full bg-slate-800 text-slate-300 border border-slate-700">
                {sessionCount}
              </span>
            )}
          </button>

          {/* New Chat Button */}
          <button
            onClick={handleStartNewChat}
            className="px-2.5 py-1.5 rounded-lg bg-slate-900 border border-slate-800 hover:border-slate-700 text-slate-300 hover:text-white text-xs font-semibold flex items-center gap-1.5 transition-all"
            title="Start New Conversation"
          >
            <Plus className="w-3.5 h-3.5 text-sky-400" />
            <span>New Chat</span>
          </button>

          {/* Current Session Title */}
          <div className="hidden sm:flex items-center gap-2 pl-2 border-l border-slate-800">
            <span className="text-xs font-semibold text-slate-200 truncate max-w-[280px]">
              {sessionTitle}
            </span>
            <span className="text-[10px] font-mono text-slate-500">
              ({activeSessionId.substring(0, 8)}...)
            </span>
          </div>
        </div>

        {/* Right Status */}
        <div className="flex items-center gap-3 text-xs">
          <span className="text-[11px] font-mono text-slate-400 hidden md:inline">
            Model: <strong className="text-sky-400">{selectedModel}</strong>
          </span>
          <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-400">
            {messages.length} {messages.length === 1 ? 'msg' : 'msgs'}
          </span>
        </div>
      </div>

      {/* 2. Persistent Chat History Drawer */}
      <ChatHistorySidebar
        isOpen={isHistoryOpen}
        onClose={() => setIsHistoryOpen(false)}
        currentSessionId={activeSessionId}
        onSelectSession={handleSelectSession}
        onNewChat={handleStartNewChat}
      />

      {/* 3. Message List with Visual Explanations & Mermaid Renderers */}
      <MessageList
        messages={messages}
        onSelectPrompt={handleSendMessage}
        onRetry={handleRetry}
        onApprove={handleApprove}
        onReject={handleReject}
      />

      {/* 4. Input Composer */}
      <ChatInput
        onSendMessage={handleSendMessage}
        onStopStream={handleStopStream}
        isStreaming={isStreaming}
        onClearSession={handleStartNewChat}
      />
    </div>
  );
};
