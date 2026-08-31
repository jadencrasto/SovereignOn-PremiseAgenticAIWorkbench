/**
 * frontend/src/components/chat/MessageItem.tsx
 * --------------------------------------------
 * Industrial Terminal Message Item (White & Light Blue Style)
 */

import React, { useState } from 'react';
import type { ChatMessage, ToolEvent } from '../../types';
import { MarkdownContent } from './MarkdownContent';
import { SourceCard } from './SourceCard';
import { Terminal, User, Copy, Check, RotateCcw, AlertTriangle, Layers, Wrench, CheckCircle2, XCircle, ImageIcon } from 'lucide-react';
import { PlanTimeline } from '../agent/PlanTimeline';
import { ApprovalCard } from '../agent/ApprovalCard';

interface MessageItemProps {
  message: ChatMessage;
  onRetry?: (content: string) => void;
  onApprove?: (taskId: string) => void;
  onReject?: (taskId: string) => void;
}

const ToolActivity: React.FC<{ events: ToolEvent[] }> = ({ events }) => {
  const [expanded, setExpanded] = useState(false);
  if (!events || events.length === 0) return null;

  const completedTools = events.filter((e) => e.type === 'tool_result');
  const runningTool = events.find(
    (e) => e.type === 'tool_start' && !events.some((r) => r.type === 'tool_result' && r.tool === e.tool)
  );

  const formatToolName = (tool: string) => {
    switch (tool) {
      case 'document_search':
        return 'BENCHMARK_RAG_SEARCH';
      case 'file_read':
        return 'WORKSPACE_FILE_INGEST';
      case 'calculator':
        return 'NUMERIC_TOLERANCE_SOLVER';
      case 'xlsx_report':
        return 'XLSX_COMPLIANCE_BUILDER';
      case 'file_write':
        return 'SANDBOX_FILE_WRITE';
      case 'artifact_verifier':
        return 'CRYPTO_SHA256_VERIFIER';
      default:
        return tool.toUpperCase();
    }
  };

  return (
    <div className="mb-4 border-2 border-[#bae6fd] bg-[#f0f9ff] font-mono text-xs">
      <div
        onClick={() => setExpanded(!expanded)}
        className="flex items-center justify-between p-3 bg-[#e0f2fe] border-b-2 border-[#bae6fd] cursor-pointer hover:bg-[#bae6fd]/50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="px-1.5 py-0.5 bg-[#0284c7] text-white font-black text-[10px] uppercase">
            {runningTool ? 'RUNNING' : 'COMPLETED'}
          </span>
          <span className="font-bold text-[#0369a1] uppercase tracking-tight">
            // AGENT TOOL PIPELINE ({completedTools.length} ACTIONS)
          </span>
        </div>
        <span className="text-[10px] font-bold text-[#0284c7] uppercase">
          [{expanded ? 'HIDE_LOGS' : 'VIEW_LOGS'}]
        </span>
      </div>

      {!expanded && completedTools.length > 0 && (
        <div className="p-2.5 flex flex-wrap gap-1.5 bg-[#f0f9ff]">
          {completedTools.map((ev, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1.5 px-2 py-0.5 bg-white border border-[#bae6fd] text-[10px] font-bold text-[#0369a1] uppercase"
            >
              <CheckCircle2 className="w-3 h-3 text-[#059669]" />
              <span>{formatToolName(ev.tool)}</span>
            </span>
          ))}
        </div>
      )}

      {expanded && (
        <div className="p-3 space-y-2 bg-[#f0f9ff]">
          {events.map((event, idx) => (
            <div key={idx} className="flex items-start gap-2 text-[11px]">
              {event.type === 'tool_start' ? (
                <>
                  <span className="w-2 h-2 bg-[#0284c7] mt-1 shrink-0" />
                  <div>
                    <span className="font-bold text-[#0284c7] uppercase">
                      &gt; {formatToolName(event.tool)}
                    </span>
                    {event.arguments && (
                      <span className="text-slate-600 ml-2">
                        {JSON.stringify(event.arguments)}
                      </span>
                    )}
                  </div>
                </>
              ) : (
                <>
                  <CheckCircle2 className="w-3.5 h-3.5 text-[#059669] shrink-0 mt-0.5" />
                  <div>
                    <span className="font-bold text-[#0f172a] uppercase">
                      &gt; {formatToolName(event.tool)} [OK]
                    </span>
                    {event.summary && (
                      <span className="ml-2 text-slate-600">{event.summary}</span>
                    )}
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}
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
      <div className="flex justify-end gap-3 max-w-5xl mx-auto px-2 py-2 font-mono">
        <div className="flex flex-col items-end max-w-[85%]">
          <div className="flex items-center gap-2 mb-1 text-[10px] font-bold text-slate-500 uppercase">
            <span>{message.timestamp}</span>
            <span className="px-1 bg-[#e0f2fe] text-[#0369a1] border border-[#bae6fd]">OP_DISPATCH</span>
          </div>

          {message.attachments && message.attachments.length > 0 && (
            <div className="mb-2 flex flex-col items-end gap-1.5 w-full">
              {message.attachments.map((att) => (
                <div key={att.id} className="max-w-xs border-2 border-black bg-white p-1 brutal-shadow-blue">
                  <img
                    src={att.objectUrl}
                    alt={att.filename}
                    className="w-full max-h-48 object-cover border border-[#cbd5e1]"
                  />
                  <div className="flex items-center justify-between px-2 py-1 text-[10px] font-bold text-[#0f172a] uppercase">
                    <span className="truncate max-w-[180px]">{att.filename}</span>
                    <span className="text-[#059669]">{(att.sizeBytes / 1024).toFixed(0)} KB</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="p-4 border-2 border-black bg-[#0284c7] text-white font-sans font-semibold text-sm leading-relaxed brutal-shadow-dark">
            <p className="whitespace-pre-wrap">{message.content}</p>
          </div>
        </div>
      </div>
    );
  }

  // Assistant Message (White & Light Blue Terminal Output)
  const hasToolEvents = message.toolEvents && message.toolEvents.length > 0;

  return (
    <div className="flex flex-col gap-2 max-w-5xl mx-auto px-2 py-3 font-mono">
      {/* Meta Header */}
      <div className="flex items-center justify-between w-full text-[11px] font-bold border-b-2 border-[#cbd5e1] pb-1.5">
        <div className="flex items-center gap-2">
          <span className="px-2 py-0.5 bg-[#0284c7] text-white font-black uppercase text-[10px]">
            SOVEREIGN_CORE
          </span>
          {message.model_used && (
            <span className="px-1.5 py-0.5 bg-[#e0f2fe] border border-[#bae6fd] text-[#0369a1] text-[10px]">
              MODEL: {message.model_used.toUpperCase()}
            </span>
          )}
        </div>
        <span className="text-[10px] text-slate-500 font-bold">{message.timestamp}</span>
      </div>

      {/* Main Terminal Output Box */}
      <div
        className={`w-full p-5 bg-white border-2 ${
          message.error
            ? 'border-[#e11d48] text-[#be123c]'
            : 'border-[#cbd5e1] text-[#0f172a] brutal-shadow-blue'
        }`}
      >
        {message.error ? (
          <div className="space-y-2">
            <div className="flex items-center gap-2 font-black text-xs text-[#e11d48] uppercase">
              <AlertTriangle className="w-4 h-4" />
              <span>[INFERENCE_EXECUTION_FAILURE]</span>
            </div>
            <p className="text-xs font-mono">{message.content}</p>
            {onRetry && (
              <button
                onClick={() => onRetry(message.content)}
                className="mt-3 px-3 py-1 bg-[#e11d48] text-white font-black text-xs uppercase border-2 border-black brutal-btn"
              >
                Retry Dispatch
              </button>
            )}
          </div>
        ) : (
          <>
            {/* Tool Activity Timeline */}
            {hasToolEvents && <ToolActivity events={message.toolEvents!} />}

            {/* Plan Timeline */}
            {message.plan && message.plan.steps && message.plan.steps.length > 0 && (
              <PlanTimeline
                taskId={message.plan.taskId}
                objective={message.plan.objective}
                steps={message.plan.steps}
              />
            )}

            {/* Approval Card */}
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
              <div className="font-sans">
                <MarkdownContent content={message.content} />
              </div>
            ) : (
              message.isStreaming && (
                <div className="flex items-center gap-2 text-xs text-[#0284c7] font-mono py-2">
                  <span className="w-2.5 h-2.5 bg-[#0284c7] animate-ping" />
                  <span className="uppercase font-bold">
                    {hasToolEvents ? 'EXECUTING AIR-GAPPED TOOLS...' : 'SYNTHESIZING DETERMINISTIC RESPONSE...'}
                  </span>
                </div>
              )
            )}

            {message.isStreaming && message.content && (
              <span className="inline-block w-2 h-4 ml-1 bg-[#0284c7] animate-pulse align-middle" />
            )}
          </>
        )}

        {/* Sources Grid */}
        {message.sources && message.sources.length > 0 && (
          <div className="mt-5 pt-4 border-t-2 border-[#e2e8f0]">
            <div className="text-[10px] font-black text-[#0284c7] uppercase tracking-widest mb-2">
              // GROUNDING BENCHMARK EVIDENCE ({message.sources.length} CHUNKS)
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {message.sources.map((src, idx) => (
                <SourceCard key={src.chunk_id || idx} source={src} />
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Copy / Actions Footer */}
      {!message.isStreaming && !message.error && message.content && (
        <div className="flex items-center justify-end gap-2 text-[10px]">
          <button
            onClick={handleCopy}
            className="px-2.5 py-1 bg-white border border-[#cbd5e1] text-slate-700 hover:text-[#0284c7] hover:border-[#0284c7] uppercase font-bold flex items-center gap-1 shadow-sm"
          >
            {copied ? (
              <>
                <Check className="w-3 h-3 text-[#059669]" />
                <span className="text-[#059669]">COPIED</span>
              </>
            ) : (
              <>
                <Copy className="w-3 h-3" />
                <span>COPY_OUTPUT</span>
              </>
            )}
          </button>
        </div>
      )}
    </div>
  );
};
