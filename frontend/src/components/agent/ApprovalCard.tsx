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
  high: { bg: 'bg-rose-50', border: 'border-rose-200', text: 'text-rose-600', badge: 'bg-rose-100' },
  medium: { bg: 'bg-amber-50', border: 'border-amber-200', text: 'text-amber-600', badge: 'bg-amber-100' },
  low: { bg: 'bg-blue-50', border: 'border-blue-200', text: 'text-blue-600', badge: 'bg-blue-100' },
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
    <div className={`my-3 rounded-lg border ${colors.border} ${colors.bg} overflow-hidden shadow-sm font-sans`}>
      {/* Header */}
      <div className="px-4 py-3 border-b border-slate-200/60 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldAlert className={`w-4 h-4 ${colors.text} animate-pulse`} />
          <span className="text-xs font-semibold text-slate-800 uppercase tracking-wider">
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
        <div className="flex items-start gap-2 p-2 rounded-md bg-white/60 border border-slate-200/60">
          <AlertTriangle className="w-4 h-4 text-amber-500 mt-0.5 shrink-0" />
          <p className="text-[11px] text-slate-700 leading-tight">
            The model requested this operation. <strong className="text-slate-900 font-semibold">The operator decides.</strong>
          </p>
        </div>

        {/* Tool info */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Wrench className="w-4 h-4 text-slate-500" />
            <span className="text-xs font-mono font-medium text-slate-800">{toolName}</span>
          </div>

          {reason && (
            <p className="text-xs text-slate-600 pl-6">{reason}</p>
          )}

          {/* Arguments */}
          {Object.keys(toolArgs).length > 0 && (
            <div className="pl-6">
              <span className="text-[10px] text-slate-500 uppercase tracking-wider font-mono font-medium">
                Arguments
              </span>
              <div className="mt-1 p-2.5 rounded-md bg-white border border-slate-200">
                {Object.entries(toolArgs).map(([key, value]) => (
                  <div key={key} className="flex gap-2 text-[11px] font-mono">
                    <span className="text-slate-500 font-semibold">{key}:</span>
                    <span className="text-slate-800 break-all">{String(value)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Expiry */}
          <div className="flex items-center gap-1.5 pl-6 text-[10px] text-slate-500 font-mono mt-2">
            <Clock className="w-3 h-3" />
            Expires: {new Date(expiresAt).toLocaleTimeString()}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-3 pt-2">
          <button
            onClick={handleApprove}
            disabled={isLoading}
            className="flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium
              bg-emerald-500 hover:bg-emerald-600 text-white
              transition-all disabled:opacity-50 disabled:cursor-not-allowed
              shadow-sm"
          >
            <ShieldCheck className="w-4 h-4" />
            Approve
          </button>
          <button
            onClick={handleReject}
            disabled={isLoading}
            className="flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium
              bg-white hover:bg-rose-50 text-rose-600 border border-rose-200 hover:border-rose-300
              transition-all disabled:opacity-50 disabled:cursor-not-allowed shadow-sm"
          >
            <ShieldX className="w-4 h-4" />
            Reject
          </button>
        </div>
      </div>

      {/* Footer */}
      <div className="px-4 py-2 border-t border-slate-200/60 bg-white/40">
        <span className="text-[10px] font-mono text-slate-400">
          Approval {approvalId.slice(0, 16)} · Step {stepId}
        </span>
      </div>
    </div>
  );
};
