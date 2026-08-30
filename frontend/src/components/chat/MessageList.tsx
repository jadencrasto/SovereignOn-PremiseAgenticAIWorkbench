import React, { useEffect, useRef } from 'react';
import type { ChatMessage } from '../../types';
import { MessageItem } from './MessageItem';
import { ShieldCheck, Database, Cpu } from 'lucide-react';

interface MessageListProps {
  messages: ChatMessage[];
  onSelectPrompt?: (prompt: string) => void;
  onRetry?: (content: string) => void;
  onApprove?: (taskId: string) => void;
  onReject?: (taskId: string) => void;
}

export const MessageList: React.FC<MessageListProps> = ({ messages, onSelectPrompt, onRetry, onApprove, onReject }) => {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-6 text-center max-w-2xl mx-auto select-none">
        <div className="w-12 h-12 rounded-2xl bg-emerald-950/60 border border-emerald-500/40 flex items-center justify-center text-emerald-400 mb-4 shadow-lg shadow-emerald-950/50">
          <ShieldCheck className="w-6 h-6" />
        </div>

        <h2 className="text-xl font-semibold text-white tracking-tight">
          Sovereign Agentic Workbench
        </h2>
        <p className="text-xs text-slate-400 mt-1 max-w-md">
          Private on-premise reasoning engine. Ingest local documents, generate embeddings via Ollama, and query vector knowledge securely.
        </p>

        {/* Demo Prompt Starter Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-6 w-full text-left">
          <button
            onClick={() => onSelectPrompt?.("Search the local documents for recurring compressor issues, summarize the key findings, and calculate the total number of recurring issues reported.")}
            className="p-3.5 rounded-xl border border-slate-800 bg-[#0d1424]/80 hover:bg-slate-800/90 hover:border-emerald-500/30 transition-all text-left group flex flex-col justify-between"
          >
            <div>
              <div className="flex items-center gap-2 mb-1">
                <Database className="w-3.5 h-3.5 text-emerald-400" />
                <span className="text-xs font-semibold text-slate-200">Task 1: RAG + Calculation</span>
              </div>
              <p className="text-[11px] text-slate-400 leading-snug line-clamp-2">
                "Search the local documents for recurring compressor issues, summarize the key findings, and calculate the total number of recurring issues reported."
              </p>
            </div>
            <div className="mt-2 text-[10px] font-mono text-emerald-400 flex items-center gap-1">
              <span>Run Task 1</span> →
            </div>
          </button>

          <button
            onClick={() => onSelectPrompt?.("Create a file named compressor_summary.txt containing a short summary of the recurring compressor issues found in the local documents.")}
            className="p-3.5 rounded-xl border border-slate-800 bg-[#0d1424]/80 hover:bg-slate-800/90 hover:border-amber-500/30 transition-all text-left group flex flex-col justify-between"
          >
            <div>
              <div className="flex items-center gap-2 mb-1">
                <ShieldCheck className="w-3.5 h-3.5 text-amber-400" />
                <span className="text-xs font-semibold text-slate-200">Task 2: Approval + File Write</span>
              </div>
              <p className="text-[11px] text-slate-400 leading-snug line-clamp-2">
                "Create a file named compressor_summary.txt containing a short summary of the recurring compressor issues found in the local documents."
              </p>
            </div>
            <div className="mt-2 text-[10px] font-mono text-amber-400 flex items-center gap-1">
              <span>Run Task 2</span> →
            </div>
          </button>

          <button
            onClick={() => onSelectPrompt?.("Search the local documents for information about aircraft engine failures and summarize the findings.")}
            className="p-3.5 rounded-xl border border-slate-800 bg-[#0d1424]/80 hover:bg-slate-800/90 hover:border-sky-500/30 transition-all text-left group flex flex-col justify-between"
          >
            <div>
              <div className="flex items-center gap-2 mb-1">
                <Cpu className="w-3.5 h-3.5 text-sky-400" />
                <span className="text-xs font-semibold text-slate-200">Out-of-Domain Relevance Test</span>
              </div>
              <p className="text-[11px] text-slate-400 leading-snug line-clamp-2">
                "Search the local documents for information about aircraft engine failures and summarize the findings."
              </p>
            </div>
            <div className="mt-2 text-[10px] font-mono text-sky-400 flex items-center gap-1">
              <span>Test Relevance Gate</span> →
            </div>
          </button>

          <button
            onClick={() => onSelectPrompt?.("Summarize the primary equipment maintenance findings across all indexed reports.")}
            className="p-3.5 rounded-xl border border-slate-800 bg-[#0d1424]/80 hover:bg-slate-800/90 hover:border-purple-500/30 transition-all text-left group flex flex-col justify-between"
          >
            <div>
              <div className="flex items-center gap-2 mb-1">
                <Database className="w-3.5 h-3.5 text-purple-400" />
                <span className="text-xs font-semibold text-slate-200">Multi-Asset Report Summary</span>
              </div>
              <p className="text-[11px] text-slate-400 leading-snug line-clamp-2">
                "Summarize the primary equipment maintenance findings across all indexed reports."
              </p>
            </div>
            <div className="mt-2 text-[10px] font-mono text-purple-400 flex items-center gap-1">
              <span>Synthesize Knowledge</span> →
            </div>
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto py-4 space-y-2">
      {messages.map((msg) => (
        <MessageItem
          key={msg.id}
          message={msg}
          onRetry={onRetry}
          onApprove={onApprove}
          onReject={onReject}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  );
};
