import React, { useState } from 'react';
import type { ChatMessage, ToolEvent } from '../../types';
import { MarkdownContent } from './MarkdownContent';
import { SourceCard } from './SourceCard';
import { Bot, User, Copy, Check, RotateCcw, AlertTriangle, Layers, Wrench, CheckCircle2, XCircle, ImageIcon } from 'lucide-react';
import { Badge } from '../common/Badge';
import { PlanTimeline } from '../agent/PlanTimeline';
import { ApprovalCard } from '../agent/ApprovalCard';

interface MessageItemProps {
  message: ChatMessage;
  onRetry?: (content: string) => void;
  onApprove?: (taskId: string) => void;
  onReject?: (taskId: string) => void;
}

// Tool Activity Timeline component
const ToolActivity: React.FC<{ events: ToolEvent[] }> = ({ events }) => {
  if (!events || events.length === 0) return null;

  return (
    <div className="mb-3 p-3 rounded-lg bg-[#0a0f1a] border border-slate-800/80">
      <div className="flex items-center gap-1.5 text-xs font-mono font-medium text-slate-400 mb-2">
        <Wrench className="w-3.5 h-3.5 text-amber-400" />
        <span>Agent Tool Activity</span>
      </div>
      <div className="space-y-1.5">
        {events.map((event, idx) => (
          <div key={idx} className="flex items-start gap-2 text-xs">
            {event.type === 'tool_start' ? (
              <>
                <span className="w-4 h-4 mt-0.5 flex items-center justify-center shrink-0">
                  <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
                </span>
                <div>
                  <span className="font-mono font-semibold text-amber-300">{event.tool}</span>
                  {event.arguments && Object.keys(event.arguments).length > 0 && (
                    <span className="text-slate-500 ml-1.5">
                      {Object.entries(event.arguments).map(([k, v]) => (
                        <span key={k} className="ml-1">
                          <span className="text-slate-600">{k}:</span>{' '}
                          <span className="text-slate-400">{String(v).substring(0, 60)}</span>
                        </span>
                      ))}
                    </span>
                  )}
                </div>
              </>
            ) : (
              <>
                {event.success ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                ) : (
                  <XCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                )}
                <div>
                  <span className="font-mono font-semibold text-slate-300">{event.tool}</span>
                  {event.summary && (
                    <span className={`ml-1.5 ${event.success ? 'text-emerald-400/80' : 'text-rose-400/80'}`}>
                      — {event.summary}
                    </span>
                  )}
                </div>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

export const MessageItem: React.FC<MessageItemProps> = ({ message, onRetry, onApprove, onReject }) => {
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

          {/* Phase 5: Image attachment thumbnail */}
          {message.attachments && message.attachments.length > 0 && (
            <div className="mb-2 flex flex-col items-end gap-1.5 w-full">
              {message.attachments.map((att) => (
                <div key={att.id} className="max-w-xs rounded-xl overflow-hidden border border-blue-600/30 shadow-sm">
                  <img
                    src={att.objectUrl}
                    alt={att.filename}
                    className="w-full max-h-48 object-cover"
                    style={{ display: 'block' }}
                  />
                  <div className="flex items-center gap-1.5 px-2 py-1 bg-blue-950/60 text-[10px] font-mono text-blue-400">
                    <ImageIcon className="w-2.5 h-2.5" />
                    <span className="truncate max-w-[180px]">{att.filename}</span>
                    <span className="text-slate-500 ml-auto">{(att.sizeBytes / 1024).toFixed(0)} KB</span>
                  </div>
                </div>
              ))}
            </div>
          )}

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
  const hasToolEvents = message.toolEvents && message.toolEvents.length > 0;

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
            {hasToolEvents && (
              <Badge variant="amber" className="text-[10px] flex items-center gap-1">
                <Wrench className="w-2.5 h-2.5" />
                {message.toolEvents!.filter(e => e.type === 'tool_result').length} Tool{message.toolEvents!.filter(e => e.type === 'tool_result').length !== 1 ? 's' : ''}
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
              {/* Tool Activity Timeline */}
              {hasToolEvents && <ToolActivity events={message.toolEvents!} />}

              {/* Phase 6: Plan Timeline */}
              {message.plan && message.plan.steps && message.plan.steps.length > 0 && (
                <PlanTimeline
                  taskId={message.plan.taskId}
                  objective={message.plan.objective}
                  steps={message.plan.steps}
                />
              )}

              {/* Phase 6: Approval Card */}
              {message.pendingApproval && onApprove && onReject && (
                <ApprovalCard
                  taskId={message.pendingApproval.task_id}
                  stepId={message.pendingApproval.step_id}
                  approvalId={message.pendingApproval.approval_id}
                  toolName={message.pendingApproval.tool_name}
                  arguments={message.pendingApproval.arguments_hash ? (message.pendingApproval as any).arguments || {} : {}}
                  riskLevel={message.pendingApproval.risk_level}
                  reason={message.pendingApproval.reason}
                  expiresAt={message.pendingApproval.expires_at}
                  onApprove={onApprove}
                  onReject={onReject}
                />
              )}

              {message.content ? (
                <MarkdownContent content={message.content} />
              ) : (
                message.isStreaming && (
                  <div className="flex items-center gap-2 text-xs text-slate-400 font-mono py-1">
                    <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                    <span>
                      {hasToolEvents ? 'Executing tools and reasoning...' : 'Synthesizing response from local model...'}
                    </span>
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
