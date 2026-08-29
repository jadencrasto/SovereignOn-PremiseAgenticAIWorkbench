import React from 'react';
import {
  CheckCircle2,
  Circle,
  Loader2,
  XCircle,
  SkipForward,
  Wrench,
  Brain,
  ShieldAlert,
} from 'lucide-react';

interface PlanStepData {
  id: string;
  description: string;
  tool_name?: string | null;
  requires_approval: boolean;
  status: string;
}

interface PlanTimelineProps {
  objective: string;
  steps: PlanStepData[];
  taskId: string;
}

const statusConfig: Record<string, {
  icon: React.FC<{ className?: string }>;
  color: string;
  label: string;
  animate?: boolean;
}> = {
  pending: { icon: Circle, color: 'text-slate-500', label: 'Pending' },
  awaiting_approval: { icon: ShieldAlert, color: 'text-amber-400', label: 'Awaiting Approval', animate: true },
  approved: { icon: CheckCircle2, color: 'text-emerald-400', label: 'Approved' },
  running: { icon: Loader2, color: 'text-sky-400', label: 'Running', animate: true },
  completed: { icon: CheckCircle2, color: 'text-emerald-400', label: 'Completed' },
  failed: { icon: XCircle, color: 'text-rose-400', label: 'Failed' },
  skipped: { icon: SkipForward, color: 'text-slate-500', label: 'Skipped' },
};

export const PlanTimeline: React.FC<PlanTimelineProps> = ({
  objective,
  steps,
  taskId,
}) => {
  return (
    <div className="my-3 rounded-lg border border-slate-700/60 bg-slate-900/60 overflow-hidden">
      {/* Header */}
      <div className="px-4 py-2.5 border-b border-slate-700/60 bg-slate-800/40">
        <div className="flex items-center gap-2 text-xs font-medium text-slate-300">
          <Brain className="w-3.5 h-3.5 text-sky-400" />
          <span className="uppercase tracking-wider text-[10px] text-sky-400 font-mono">
            Execution Plan
          </span>
        </div>
        <p className="text-xs text-slate-400 mt-1 line-clamp-2">{objective}</p>
      </div>

      {/* Steps */}
      <div className="px-4 py-2 space-y-0">
        {steps.map((step, idx) => {
          const config = statusConfig[step.status] || statusConfig.pending;
          const Icon = config.icon;

          return (
            <div key={step.id} className="flex items-start gap-3 py-2">
              {/* Timeline connector */}
              <div className="flex flex-col items-center pt-0.5">
                <Icon
                  className={`w-4 h-4 ${config.color} ${config.animate ? 'animate-pulse' : ''} shrink-0`}
                />
                {idx < steps.length - 1 && (
                  <div className="w-px h-full min-h-[16px] bg-slate-700/60 mt-1" />
                )}
              </div>

              {/* Step content */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-slate-300 font-medium truncate">
                    {step.description}
                  </span>
                </div>
                <div className="flex items-center gap-2 mt-0.5">
                  {step.tool_name && (
                    <span className="inline-flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700/60">
                      <Wrench className="w-2.5 h-2.5" />
                      {step.tool_name}
                    </span>
                  )}
                  {step.requires_approval && (
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-950/50 text-amber-400 border border-amber-500/30">
                      Approval Required
                    </span>
                  )}
                  <span className={`text-[10px] font-mono ${config.color}`}>
                    {config.label}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Footer */}
      <div className="px-4 py-1.5 border-t border-slate-700/40 bg-slate-800/20">
        <span className="text-[10px] font-mono text-slate-500">
          Task {taskId} · {steps.length} steps
        </span>
      </div>
    </div>
  );
};
