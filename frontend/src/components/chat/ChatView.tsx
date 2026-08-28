import React, { useState, useRef, useCallback } from 'react';
import type { ChatMessage, SourceReference } from '../../types';
import { MessageList } from './MessageList';
import { ChatInput } from './ChatInput';
import { useWorkbench } from '../../context/WorkbenchContext';
import { streamChat } from '../../api/chat';

export const ChatView: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const { sessionId, resetSession, selectedModel, addToast } = useWorkbench();

  const handleSendMessage = useCallback(
    async (text: string) => {
      if (!text.trim() || isStreaming) return;

      const userTimestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const userMsg: ChatMessage = {
        id: 'msg_' + Math.random().toString(36).substring(2, 9),
        role: 'user',
        content: text,
        timestamp: userTimestamp,
      };

      const assistantId = 'asst_' + Math.random().toString(36).substring(2, 9);
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: 'assistant',
        content: '',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        model_used: selectedModel,
        sources: [],
        isStreaming: true,
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setIsStreaming(true);

      const controller = new AbortController();
      abortControllerRef.current = controller;

      try {
        await streamChat(
          {
            session_id: sessionId,
            message: text,
            model: selectedModel,
            stream: true,
          },
          {
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
                prev.map((msg) =>
                  msg.id === assistantId ? { ...msg, sources } : msg
                )
              );
            },
            onDone: (_returnedSessionId: string, modelUsed: string) => {
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantId
                    ? {
                        ...msg,
                        isStreaming: false,
                        model_used: modelUsed || selectedModel,
                      }
                    : msg
                )
              );
              setIsStreaming(false);
            },
            onError: (errorText: string) => {
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === assistantId
                    ? {
                        ...msg,
                        content: errorText,
                        error: true,
                        isStreaming: false,
                      }
                    : msg
                )
              );
              setIsStreaming(false);
              addToast('error', `Chat error: ${errorText}`);
            },
          },
          controller.signal
        );
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
    setMessages([]);
    resetSession();
  };

  const handleRetry = () => {
    const lastUserMessage = [...messages].reverse().find((m) => m.role === 'user');
    if (lastUserMessage) {
      handleSendMessage(lastUserMessage.content);
    }
  };

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
