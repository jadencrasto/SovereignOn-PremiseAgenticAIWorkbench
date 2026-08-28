import React, { useState } from 'react';
import type { ChatMessage } from '../../types';
import { MarkdownContent } from './MarkdownContent';
import { SourceCard } from './SourceCard';
import { Bot, User, Copy, Check, RotateCcw, AlertTriangle, Layers } from 'lucide-react';
import { Badge } from '../common/Badge';

interface MessageItemProps {
  message: ChatMessage;
  onRetry?: (content: string) => void;
}

export const MessageItem: React.FC<MessageItemProps> = ({ message, onRetry }) => {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === 'user';

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy', err);
    }
  };

  if (isUser) {
    return (
      <div className="flex justify-end gap-3 max-w-4xl mx-auto px-4 py-2">
        <div className="flex flex-col items-end max-w-[80%]">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[10px] font-mono text-slate-500">{message.timestamp}</span>
            <span className="text-xs font-semibold text-slate-300">Operator</span>
          </div>
          <div className="p-3.5 rounded-xl rounded-tr-sm bg-blue-600/20 border border-blue-500/30 text-slate-100 text-sm leading-relaxed shadow-sm">
            <p className="whitespace-pre-wrap">{message.content}</p>
          </div>
        </div>
        <div className="w-8 h-8 rounded-lg bg-blue-950/80 border border-blue-600/40 flex items-center justify-center text-blue-400 shrink-0 mt-1">
          <User className="w-4 h-4" />
        </div>
      </div>
    );
  }

  // Assistant Message
  return (
    <div className="flex gap-3.5 max-w-4xl mx-auto px-4 py-3">
      {/* Avatar */}
      <div className="w-8 h-8 rounded-lg bg-emerald-950/80 border border-emerald-500/40 flex items-center justify-center text-emerald-400 shrink-0 mt-1 shadow-sm">
        <Bot className="w-4.5 h-4.5" />
      </div>

      {/* Message Card */}
      <div className="flex-1 min-w-0 flex flex-col items-start">
        {/* Header bar */}
        <div className="flex items-center justify-between w-full mb-1.5 text-xs">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-white tracking-tight">Sovereign Assistant</span>
            {message.model_used && (
              <Badge variant="slate" className="text-[10px]">
                {message.model_used}
              </Badge>
            )}
            {message.sources && message.sources.length > 0 && (
              <Badge variant="emerald" className="text-[10px] flex items-center gap-1">
                <Layers className="w-2.5 h-2.5" />
                {message.sources.length} {message.sources.length === 1 ? 'Source' : 'Sources'}
              </Badge>
            )}
          </div>
          <span className="text-[10px] font-mono text-slate-500">{message.timestamp}</span>
        </div>

        {/* Content Box */}
        <div
          className={`w-full p-4 rounded-xl rounded-tl-sm border text-sm leading-relaxed ${
            message.error
              ? 'bg-rose-950/20 border-rose-800/50 text-rose-200'
              : 'bg-[#0f172a]/90 border-slate-800/90 text-slate-200 shadow-md'
          }`}
        >
          {message.error ? (
            <div className="flex items-start gap-2.5">
              <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
              <div>
                <p className="font-medium text-rose-300">Model Inference Error</p>
                <p className="text-xs text-rose-400/90 mt-1">{message.content}</p>
                {onRetry && (
                  <button
                    onClick={() => onRetry(message.content)}
                    className="mt-3 inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-mono bg-rose-900/50 hover:bg-rose-900 border border-rose-700/50 text-rose-200 transition-colors"
                  >
                    <RotateCcw className="w-3 h-3" /> Retry Generation
                  </button>
                )}
              </div>
            </div>
          ) : (
            <>
              {message.content ? (
                <MarkdownContent content={message.content} />
              ) : (
                message.isStreaming && (
                  <div className="flex items-center gap-2 text-xs text-slate-400 font-mono py-1">
                    <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                    <span>Synthesizing response from local model...</span>
                  </div>
                )
              )}

              {/* Streaming Cursor Dot */}
              {message.isStreaming && message.content && (
                <span className="inline-block w-1.5 h-4 ml-1 align-middle bg-emerald-400 animate-pulse" />
              )}
            </>
          )}

          {/* RAG Sources Section */}
          {message.sources && message.sources.length > 0 && (
            <div className="mt-4 pt-3.5 border-t border-slate-800/80">
              <div className="flex items-center gap-1.5 text-xs font-mono font-medium text-slate-400 mb-2">
                <Layers className="w-3.5 h-3.5 text-emerald-400" />
                <span>Grounding Evidence ({message.sources.length} chunks retrieved from local vector store)</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {message.sources.map((src, idx) => (
                  <SourceCard key={src.chunk_id || idx} source={src} />
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer actions */}
        {!message.isStreaming && !message.error && message.content && (
          <div className="flex items-center gap-2 mt-1.5 ml-1 text-slate-500">
            <button
              onClick={handleCopy}
              className="flex items-center gap-1 text-[11px] hover:text-slate-300 transition-colors px-1.5 py-0.5 rounded hover:bg-slate-800/40"
              title="Copy message"
            >
              {copied ? (
                <>
                  <Check className="w-3 h-3 text-emerald-400" />
                  <span className="text-emerald-400 font-mono">Copied</span>
                </>
              ) : (
                <>
                  <Copy className="w-3 h-3" />
                  <span className="font-mono">Copy</span>
                </>
              )}
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
