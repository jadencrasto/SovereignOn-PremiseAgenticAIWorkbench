import React, { useState, useRef, useCallback } from 'react';
import type { ChatMessage, ImageAttachment, SourceReference, ToolEvent } from '../../types';
import { MessageList } from './MessageList';
import { ChatInput } from './ChatInput';
import { useWorkbench } from '../../context/WorkbenchContext';
import { streamChat, streamChatMultimodal } from '../../api/chat';
import { approveTaskStep, rejectTaskStep } from '../../api/tasks';

export const ChatView: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const { sessionId, resetSession, selectedModel, addToast } = useWorkbench();

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

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setIsStreaming(true);

      const controller = new AbortController();
      abortControllerRef.current = controller;

      const callbacks = {
        onDelta: (delta: string) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? { ...msg, content: msg.content + delta, isStreaming: true }
                : msg
            )
          );
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
        // Phase 5: agent_status updates
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
                          ? '🧠 Reasoning'
                          : `⚙ ${status}`,
                        arguments: {},
                      },
                    ],
                  }
                : msg
            )
          );
        },
        // Phase 6: Plan created event
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
        // Phase 6: Plan step update
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
        // Phase 6: Approval required
        onApprovalRequired: (approvalData: any) => {
          setMessages((prev) =>
            prev.map((msg) =>
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
            )
          );
          setIsStreaming(false);
          addToast('info', `Human approval required for tool: ${approvalData.tool_name}`);
        },
        // Phase 6: Approval resolved
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
        // Phase 6: Task completed/failed
        onTaskStatus: (status: string, _taskId: string) => {
          if (status === 'task_completed' || status === 'task_failed' || status === 'task_cancelled') {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === assistantId
                  ? { ...msg, isStreaming: false }
                  : msg
              )
            );
            setIsStreaming(false);
          }
        },
        onDone: (_returnedSessionId: string, modelUsed: string) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? { ...msg, isStreaming: false, model_used: modelUsed || selectedModel }
                : msg
            )
          );
          setIsStreaming(false);
        },
        onError: (errorText: string) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? { ...msg, content: errorText, error: true, isStreaming: false }
                : msg
            )
          );
          setIsStreaming(false);
          addToast('error', `Chat error: ${errorText}`);
        },
      };

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
            callbacks,
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
            callbacks,
            controller.signal
          );
        }
      } catch (err: any) {
        if (err.name !== 'AbortError') {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantId
                ? {
                    ...msg,
                    content: err.message || 'Stream connection failed',
                    error: true,
                    isStreaming: false,
                  }
                : msg
            )
          );
        }
        setIsStreaming(false);
      } finally {
        abortControllerRef.current = null;
      }
    },
    [sessionId, selectedModel, isStreaming, addToast]
  );

  /**
   * Handle human approval of a pending task step
   */
  const handleApprove = async (taskId: string) => {
    try {
      addToast('info', 'Submitting approval...');
      // Clear pending approval from the message
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

      // Process the resumed SSE stream
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
            } else if (data.type === 'approval_required') {
              setMessages((prev) =>
                prev.map((m) =>
                  m.plan?.taskId === taskId
                    ? {
                        ...m,
                        pendingApproval: {
                          approval_id: data.approval_id,
                          task_id: data.task_id,
                          step_id: data.step_id,
                          tool_name: data.tool_name,
                          arguments_hash: data.arguments_hash || '',
                          risk_level: data.risk_level || 'high',
                          reason: data.reason || '',
                          status: 'pending',
                          created_at: new Date().toISOString(),
                          expires_at: data.expires_at || '',
                          ...((data.arguments && { arguments: data.arguments }) as any),
                        },
                      }
                    : m
                )
              );
            }
          } catch {}
        }
      }
      setIsStreaming(false);
      addToast('success', 'Step approved and executed.');
    } catch (err: any) {
      addToast('error', `Approval execution failed: ${err.message}`);
      setIsStreaming(false);
    }
  };

  /**
   * Handle human rejection of a pending task step
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
      setMessages((prev) =>
        prev.map((msg) => (msg.isStreaming ? { ...msg, isStreaming: false } : msg))
      );
      addToast('info', 'Generation interrupted.');
    }
  };

  const handleClearSession = () => {
    handleStopStream();
    messages.forEach((msg) => {
      msg.attachments?.forEach((att) => {
        if (att.objectUrl) URL.revokeObjectURL(att.objectUrl);
      });
    });
    setMessages([]);
    resetSession();
  };

  const handleRetry = () => {
    const lastUserMessage = [...messages].reverse().find((m) => m.role === 'user');
    if (lastUserMessage) {
      handleSendMessage(lastUserMessage.content);
    }
  };

  // Listen to Demo Scenario triggers
  React.useEffect(() => {
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
          // Attempt to load the preloaded image blob from server
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
    <div className="flex-1 flex flex-col h-full overflow-hidden bg-[#090d16]">
      {/* Workspace Header */}
      <div className="h-12 border-b border-slate-800/80 px-6 flex items-center justify-between shrink-0 bg-[#0c121e]/80 backdrop-blur-md">
        <div className="flex items-center gap-3">
          <h1 className="text-sm font-semibold text-white tracking-tight">Agent Conversation</h1>
          <span className="text-[11px] font-mono text-slate-500">Session: {sessionId.substring(0, 8)}...</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-slate-400">
            {messages.length} {messages.length === 1 ? 'Message' : 'Messages'}
          </span>
        </div>
      </div>

      {/* Message List */}
      <MessageList
        messages={messages}
        onSelectPrompt={handleSendMessage}
        onRetry={handleRetry}
        onApprove={handleApprove}
        onReject={handleReject}
      />

      {/* Input composer */}
      <ChatInput
        onSendMessage={handleSendMessage}
        onStopStream={handleStopStream}
        isStreaming={isStreaming}
        onClearSession={handleClearSession}
      />
    </div>
  );
};
