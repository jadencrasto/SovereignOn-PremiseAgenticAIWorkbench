import React, { useState } from 'react';
import {
  ShieldAlert,
  ShieldCheck,
  ShieldX,
  Wrench,
  Clock,
  AlertTriangle,
} from 'lucide-react';

interface ApprovalCardProps {
  taskId: string;
  stepId: string;
  approvalId: string;
  toolName: string;
  arguments: Record<string, string>;
  riskLevel: string;
  reason: string;
  expiresAt: string;
  onApprove: (taskId: string) => void;
  onReject: (taskId: string) => void;
}

const riskColors: Record<string, { bg: string; border: string; text: string; badge: string }> = {
  high: { bg: 'bg-rose-950/30', border: 'border-rose-500/40', text: 'text-rose-400', badge: 'bg-rose-500/20' },
  medium: { bg: 'bg-amber-950/30', border: 'border-amber-500/40', text: 'text-amber-400', badge: 'bg-amber-500/20' },
  low: { bg: 'bg-sky-950/30', border: 'border-sky-500/40', text: 'text-sky-400', badge: 'bg-sky-500/20' },
};

export const ApprovalCard: React.FC<ApprovalCardProps> = ({
  taskId,
  stepId,
  approvalId,
  toolName,
  arguments: toolArgs,
  riskLevel,
  reason,
  expiresAt,
  onApprove,
  onReject,
}) => {
  const [isLoading, setIsLoading] = useState(false);
  const colors = riskColors[riskLevel] || riskColors.medium;

  const handleApprove = () => {
    setIsLoading(true);
    onApprove(taskId);
  };

  const handleReject = () => {
    setIsLoading(true);
    onReject(taskId);
  };

  return (
    <div className={`my-3 rounded-lg border ${colors.border} ${colors.bg} overflow-hidden`}>
      {/* Header */}
      <div className="px-4 py-3 border-b border-slate-700/40 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldAlert className={`w-4 h-4 ${colors.text} animate-pulse`} />
          <span className="text-xs font-semibold text-white uppercase tracking-wider">
            Approval Required
          </span>
        </div>
        <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full ${colors.badge} ${colors.text} border ${colors.border}`}>
          {riskLevel.toUpperCase()} RISK
        </span>
      </div>

      {/* Body */}
      <div className="px-4 py-3 space-y-3">
        {/* Operator notice */}
        <div className="flex items-start gap-2 p-2 rounded bg-slate-800/40 border border-slate-700/40">
          <AlertTriangle className="w-3.5 h-3.5 text-amber-400 mt-0.5 shrink-0" />
          <p className="text-[11px] text-slate-300 leading-tight">
            The model requested this operation. <strong className="text-white">The operator decides.</strong>
          </p>
        </div>

        {/* Tool info */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Wrench className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-xs font-mono text-slate-300">{toolName}</span>
          </div>

          {reason && (
            <p className="text-xs text-slate-400 pl-5">{reason}</p>
          )}

          {/* Arguments */}
          {Object.keys(toolArgs).length > 0 && (
            <div className="pl-5">
              <span className="text-[10px] text-slate-500 uppercase tracking-wider font-mono">
                Arguments
              </span>
              <div className="mt-1 p-2 rounded bg-slate-900/60 border border-slate-700/40">
                {Object.entries(toolArgs).map(([key, value]) => (
                  <div key={key} className="flex gap-2 text-[11px] font-mono">
                    <span className="text-slate-500">{key}:</span>
                    <span className="text-slate-300 break-all">{String(value)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Expiry */}
          <div className="flex items-center gap-1.5 pl-5 text-[10px] text-slate-500 font-mono">
            <Clock className="w-3 h-3" />
            Expires: {new Date(expiresAt).toLocaleTimeString()}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={handleApprove}
            disabled={isLoading}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-xs font-medium
              bg-emerald-600/80 hover:bg-emerald-500/90 text-white border border-emerald-500/40
              transition-all disabled:opacity-50 disabled:cursor-not-allowed
              shadow-sm shadow-emerald-950/50"
          >
            <ShieldCheck className="w-3.5 h-3.5" />
            Approve
          </button>
          <button
            onClick={handleReject}
            disabled={isLoading}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-xs font-medium
              bg-rose-950/60 hover:bg-rose-900/80 text-rose-300 border border-rose-500/30
              transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <ShieldX className="w-3.5 h-3.5" />
            Reject
          </button>
        </div>
      </div>

      {/* Footer */}
      <div className="px-4 py-1.5 border-t border-slate-700/40 bg-slate-800/20">
        <span className="text-[10px] font-mono text-slate-500">
          Approval {approvalId.slice(0, 16)} · Step {stepId}
        </span>
      </div>
    </div>
  );
};
