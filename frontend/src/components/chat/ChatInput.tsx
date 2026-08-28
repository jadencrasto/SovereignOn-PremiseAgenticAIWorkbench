import React, { useState, useRef, useEffect } from 'react';
import { Send, Square, Trash2, Cpu, FileText } from 'lucide-react';
import { useWorkbench } from '../../context/WorkbenchContext';

interface ChatInputProps {
  onSendMessage: (message: string) => void;
  onStopStream?: () => void;
  isStreaming: boolean;
  onClearSession: () => void;
}

export const ChatInput: React.FC<ChatInputProps> = ({
  onSendMessage,
  onStopStream,
  isStreaming,
  onClearSession,
}) => {
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { availableModels, selectedModel, setSelectedModel, documents, isBackendConnected } =
    useWorkbench();

  useEffect(() => {
    if (!isStreaming && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [isStreaming]);

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`;
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleSubmit = () => {
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;
    onSendMessage(trimmed);
    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  return (
    <div className="p-4 bg-[#090d16]/90 border-t border-slate-800/80 backdrop-blur-md shrink-0">
      <div className="max-w-4xl mx-auto space-y-2">
        {/* Controls header */}
        <div className="flex items-center justify-between text-xs font-mono text-slate-400 px-1">
          {/* Model selector */}
          <div className="flex items-center gap-2">
            <Cpu className="w-3.5 h-3.5 text-blue-400" />
            <span className="text-slate-500">Model:</span>
            {availableModels.length > 0 ? (
              <select
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                disabled={isStreaming}
                className="bg-slate-900 border border-slate-700/80 rounded px-2 py-0.5 text-xs text-slate-200 focus:outline-none focus:border-emerald-500 cursor-pointer"
              >
                {availableModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            ) : (
              <span className="text-slate-300">{selectedModel || 'ollama/qwen2.5:7b'}</span>
            )}

            {/* Document grounding badge */}
            {documents.length > 0 && (
              <span className="hidden sm:inline-flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 text-[10px]">
                <FileText className="w-3 h-3" />
                <span>RAG Active ({documents.length} docs)</span>
              </span>
            )}
          </div>

          {/* New Session Button */}
          <button
            onClick={onClearSession}
            disabled={isStreaming}
            className="flex items-center gap-1 hover:text-slate-200 transition-colors px-2 py-1 rounded hover:bg-slate-800/50 disabled:opacity-50"
            title="Reset conversation state"
          >
            <Trash2 className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-[11px]">New Session</span>
          </button>
        </div>

        {/* Textarea & Send */}
        <div className="relative flex items-end rounded-xl border border-slate-700/80 bg-[#0f172a] shadow-inner focus-within:border-emerald-500/70 focus-within:ring-1 focus-within:ring-emerald-500/30 transition-all">
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            disabled={!isBackendConnected || isStreaming}
            placeholder={
              !isBackendConnected
                ? 'Backend offline — ensure "uvicorn backend.main:app" is running...'
                : documents.length > 0
                ? 'Ask a question grounded in local documents (e.g. "What is the refund policy?")...'
                : 'Send instruction to Sovereign Agent...'
            }
            className="w-full resize-none bg-transparent py-3.5 pl-4 pr-12 text-sm text-slate-100 placeholder-slate-500 focus:outline-none max-h-44 disabled:cursor-not-allowed leading-relaxed"
          />

          <div className="absolute right-2 bottom-2">
            {isStreaming ? (
              <button
                type="button"
                onClick={onStopStream}
                className="w-8 h-8 rounded-lg bg-rose-600 hover:bg-rose-500 text-white flex items-center justify-center transition-colors shadow-md"
                title="Stop generation"
              >
                <Square className="w-3.5 h-3.5 fill-current" />
              </button>
            ) : (
              <button
                type="button"
                onClick={handleSubmit}
                disabled={!input.trim() || !isBackendConnected}
                className="w-8 h-8 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-800 disabled:text-slate-600 text-white flex items-center justify-center transition-colors shadow-md disabled:shadow-none"
                title="Send message (Enter)"
              >
                <Send className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between text-[10.5px] font-mono text-slate-500 px-1">
          <span>Press Enter to send, Shift + Enter for new line</span>
          <span>Zero external telemetry</span>
        </div>
      </div>
    </div>
  );
};
