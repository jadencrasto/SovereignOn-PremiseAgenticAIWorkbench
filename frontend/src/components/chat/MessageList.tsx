import React, { useEffect, useRef } from 'react';
import type { ChatMessage } from '../../types';
import { MessageItem } from './MessageItem';
import { ShieldCheck, Database, Cpu } from 'lucide-react';
import { useWorkbench } from '../../context/WorkbenchContext';

interface MessageListProps {
  messages: ChatMessage[];
  onSelectPrompt?: (prompt: string) => void;
  onRetry?: (content: string) => void;
  onApprove?: (taskId: string) => void;
  onReject?: (taskId: string) => void;
}

export const MessageList: React.FC<MessageListProps> = ({ messages, onSelectPrompt, onRetry, onApprove, onReject }) => {
  const bottomRef = useRef<HTMLDivElement>(null);
  const { documents, selectedModel } = useWorkbench();

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  if (messages.length === 0) {
    const suggestions = [
      {
        title: 'Document Reasoning',
        desc: documents.length > 0 ? `Ask questions grounded in your ${documents.length} indexed documents.` : 'Upload a PDF/TXT/MD/DOCX in Documents tab to test RAG.',
        prompt: documents.length > 0 ? 'Summarize the main points from the uploaded documents.' : 'What is the refund policy in the reference manual?',
        icon: Database,
      },
      {
        title: 'Local Intelligence',
        desc: `Test on-premise inference with ${selectedModel}.`,
        prompt: 'Explain the core principles of sovereign on-premise AI systems in 3 concise bullet points.',
        icon: Cpu,
      },
      {
        title: 'Architecture Verification',
        desc: 'Inspect how the system handles prompt grounding & security.',
        prompt: 'In one sentence: how does this workbench guarantee data sovereignty?',
        icon: ShieldCheck,
      },
    ];

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

        {/* Suggestion Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-8 w-full">
          {suggestions.map((item, idx) => {
            const Icon = item.icon;
            return (
              <button
                key={idx}
                onClick={() => onSelectPrompt?.(item.prompt)}
                className="p-3.5 rounded-xl border border-slate-800 bg-[#0d1424]/70 hover:bg-slate-800/80 hover:border-slate-700 text-left transition-all group flex flex-col justify-between"
              >
                <div>
                  <div className="flex items-center gap-2 mb-1.5">
                    <Icon className="w-4 h-4 text-emerald-400 group-hover:scale-110 transition-transform" />
                    <span className="text-xs font-medium text-slate-200">{item.title}</span>
                  </div>
                  <p className="text-[11px] text-slate-400 leading-snug">{item.desc}</p>
                </div>
                <div className="mt-3 text-[10px] font-mono text-emerald-400/90 flex items-center gap-1">
                  <span>Send prompt</span> →
                </div>
              </button>
            );
          })}
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
