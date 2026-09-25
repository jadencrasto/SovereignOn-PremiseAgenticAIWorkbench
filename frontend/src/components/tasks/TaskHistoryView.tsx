import React, { useEffect, useState } from 'react';
import {
  ListTodo,
  RefreshCw,
  Clock,
  CheckCircle2,
  XCircle,
  AlertCircle,
  ShieldAlert,
  Loader2,
  ChevronRight,
  Filter,
} from 'lucide-react';
import { fetchTasks, fetchTask } from '../../api/tasks';
import type { TaskSummary, TaskDetail } from '../../types';
import { PlanTimeline } from '../agent/PlanTimeline';

export const TaskHistoryView: React.FC = () => {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selectedTask, setSelectedTask] = useState<TaskDetail | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isLoadingDetail, setIsLoadingDetail] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const loadTasks = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetchTasks(50, statusFilter || undefined);
      setTasks(res.tasks || []);
    } catch (err: any) {
      setError(err.message || 'Failed to load tasks');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadTasks();
  }, [statusFilter]);

  const handleSelectTask = async (taskId: string) => {
    setIsLoadingDetail(true);
    try {
      const detail = await fetchTask(taskId);
      setSelectedTask(detail);
    } catch (err: any) {
      setError(err.message || 'Failed to load task details');
    } finally {
      setIsLoadingDetail(false);
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'completed':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <CheckCircle2 className="w-3 h-3" /> Completed
          </span>
        );
      case 'failed':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-rose-500/10 text-rose-400 border border-rose-500/20">
            <XCircle className="w-3 h-3" /> Failed
          </span>
        );
      case 'awaiting_approval':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20">
            <ShieldAlert className="w-3 h-3 animate-pulse" /> Awaiting Approval
          </span>
        );
      case 'executing':
      case 'planning':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-sky-500/10 text-sky-400 border border-sky-500/20">
            <Loader2 className="w-3 h-3 animate-spin" /> {status.toUpperCase()}
          </span>
        );
      case 'cancelled':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-slate-100 text-slate-600 border border-slate-200">
            <AlertCircle className="w-3 h-3" /> Cancelled
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-slate-100 text-slate-600 border border-slate-200">
            <Clock className="w-3 h-3" /> {status}
          </span>
        );
    }
  };

  return (
    <div className="flex h-full flex-col bg-slate-50 text-slate-800 font-sans">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4 bg-white">
        <div>
          <h1 className="text-lg font-semibold flex items-center gap-2 text-slate-900 tracking-tight">
            <ListTodo className="w-5 h-5 text-blue-500" />
            Agent Task History
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Audit log of autonomous plans, approvals, and persistent execution state.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 bg-white border border-slate-200 rounded-lg px-3 py-1.5 text-sm text-slate-700 shadow-sm">
            <Filter className="w-4 h-4 text-slate-500" />
            <select
              className="bg-transparent text-sm focus:outline-none cursor-pointer"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="" className="bg-white">All Statuses</option>
              <option value="completed" className="bg-white">Completed</option>
              <option value="awaiting_approval" className="bg-white">Awaiting Approval</option>
              <option value="executing" className="bg-white">Executing</option>
              <option value="failed" className="bg-white">Failed</option>
              <option value="cancelled" className="bg-white">Cancelled</option>
            </select>
          </div>
          <button
            onClick={loadTasks}
            disabled={isLoading}
            className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium bg-white hover:bg-slate-50 border border-slate-200 rounded-lg shadow-sm transition-colors text-slate-700 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin text-blue-500' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Task List */}
        <div className="w-1/2 border-r border-slate-200 overflow-y-auto p-5 space-y-3 bg-slate-50/50">
          {error && (
            <div className="p-3 mb-3 bg-rose-50 border border-rose-200 rounded-lg text-sm text-rose-700 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              {error}
            </div>
          )}

          {isLoading ? (
            <div className="flex flex-col items-center justify-center h-48 text-slate-500 text-sm">
              <Loader2 className="w-6 h-6 animate-spin text-blue-500 mb-2" />
              Loading persistent tasks...
            </div>
          ) : tasks.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 text-slate-500 text-sm text-center border border-dashed border-slate-300 rounded-lg p-6 bg-white">
              <ListTodo className="w-8 h-8 text-slate-500 mb-3" />
              <p>No agent tasks recorded yet.</p>
              <p className="text-xs text-slate-500 mt-1">
                Multi-step agent queries will generate persistent execution plans here.
              </p>
            </div>
          ) : (
            tasks.map((task) => {
              const isSelected = selectedTask?.task_id === task.task_id;
              return (
                <div
                  key={task.task_id}
                  onClick={() => handleSelectTask(task.task_id)}
                  className={`p-4 rounded-xl border text-left cursor-pointer transition-all ${
                    isSelected
                      ? 'bg-white border-blue-500 shadow-sm ring-1 ring-blue-500/10'
                      : 'bg-white hover:bg-slate-50 hover:border-slate-300 border-slate-200 shadow-sm'
                  }`}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-[11px] font-semibold text-slate-500 uppercase">
                      {task.task_id}
                    </span>
                    {getStatusBadge(task.status)}
                  </div>
                  <p className="text-sm text-slate-800 font-medium line-clamp-2 mb-3 leading-relaxed">
                    {task.user_request}
                  </p>
                  <div className="flex items-center justify-between text-xs text-slate-500">
                    <div className="flex items-center gap-2 font-medium">
                      <span>{task.completed_steps}/{task.step_count} steps</span>
                      <span>•</span>
                      <span>{new Date(task.created_at).toLocaleTimeString()}</span>
                    </div>
                    <ChevronRight className="w-4 h-4 text-slate-400" />
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Right: Task Details */}
        <div className="w-1/2 overflow-y-auto p-6 bg-white">
          {isLoadingDetail ? (
            <div className="flex flex-col items-center justify-center h-full text-slate-500 text-sm">
              <Loader2 className="w-6 h-6 animate-spin text-blue-500 mb-2" />
              Loading task details...
            </div>
          ) : selectedTask ? (
            <div className="space-y-6">
              <div>
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase text-slate-500 tracking-wider">
                    Task {selectedTask.task_id}
                  </span>
                  {getStatusBadge(selectedTask.status)}
                </div>
                <h2 className="text-base font-bold text-slate-900 mt-2 leading-snug">
                  {selectedTask.user_request}
                </h2>
                <div className="flex items-center gap-4 text-xs text-slate-500 mt-3 font-medium">
                  <span>Session: {selectedTask.session_id.slice(0, 8)}...</span>
                  <span>Created: {new Date(selectedTask.created_at).toLocaleTimeString()}</span>
                </div>
              </div>

              {/* Dynamic Lifecycle Stepper */}
              <div className="p-4 rounded-xl bg-slate-50 border border-slate-200 space-y-2 shadow-sm">
                <div className="text-xs font-bold uppercase text-slate-500 tracking-wider">
                  Task Lifecycle
                </div>
                {(() => {
                  const hasApproval = selectedTask.plan?.steps?.some((s: any) => s.requires_approval);
                  const isTerminal = ['completed', 'failed', 'cancelled'].includes(selectedTask.status);
                  const hasResult = !!selectedTask.result;
                  const hasPlan = !!selectedTask.plan;

                  // Build lifecycle stages dynamically from actual task state
                  const stages: Array<{ label: string; active: boolean; pulsing?: boolean }> = [
                    { label: 'Request', active: true },
                    { label: 'Plan', active: hasPlan },
                    ...(hasApproval
                      ? [{
                          label: 'Approval',
                          active: selectedTask.status !== 'awaiting_approval' && isTerminal,
                          pulsing: selectedTask.status === 'awaiting_approval',
                        }]
                      : [{ label: 'Approval (N/A)', active: false }]
                    ),
                    {
                      label: 'Execution',
                      active: selectedTask.status === 'executing' || isTerminal,
                      pulsing: selectedTask.status === 'executing',
                    },
                    { label: 'Result', active: hasResult || isTerminal },
                    { label: 'Audit', active: isTerminal },
                  ];

                  return (
                    <div className="flex items-center gap-2 text-xs font-medium overflow-x-auto py-1">
                      {stages.map((stage, idx) => (
                        <React.Fragment key={stage.label}>
                          {idx > 0 && <span className="text-slate-500">→</span>}
                          <span className={`px-2.5 py-1 rounded-md whitespace-nowrap ${
                            stage.pulsing
                              ? 'bg-amber-100 text-amber-700 border border-amber-300 animate-pulse'
                              : stage.active
                                ? selectedTask.status === 'failed' && stage.label === 'Execution'
                                  ? 'bg-rose-100 text-rose-700 border border-rose-300'
                                  : 'bg-emerald-100 text-emerald-700 border border-emerald-300'
                                : stage.label.includes('N/A')
                                  ? 'bg-slate-100 text-slate-500 border border-slate-200 italic'
                                  : 'bg-slate-200 text-slate-600 border border-slate-300'
                          }`}>
                            {stage.label}
                          </span>
                        </React.Fragment>
                      ))}
                    </div>
                  );
                })()}
              </div>

              {selectedTask.plan && selectedTask.plan.steps && (
                <div>
                  <h3 className="text-xs font-bold text-slate-800 mb-3 uppercase tracking-wider">
                    Plan Timeline
                  </h3>
                  <PlanTimeline
                    taskId={selectedTask.task_id}
                    objective={selectedTask.plan.objective}
                    steps={selectedTask.plan.steps}
                  />
                </div>
              )}

              {selectedTask.result && (
                <div>
                  <h3 className="text-xs font-bold text-slate-800 mb-3 uppercase tracking-wider">
                    Final Result
                  </h3>
                  <div className="p-4 rounded-xl bg-slate-50 border border-slate-200 text-sm text-slate-700 whitespace-pre-wrap leading-relaxed shadow-sm">
                    {selectedTask.result}
                  </div>
                </div>
              )}

              {selectedTask.error && (
                <div>
                  <h3 className="text-xs font-bold text-rose-600 mb-3 uppercase tracking-wider">
                    Error Log
                  </h3>
                  <div className="p-4 rounded-xl bg-rose-50 border border-rose-200 text-sm text-rose-700 whitespace-pre-wrap font-mono shadow-sm">
                    {selectedTask.error}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-slate-500 text-sm text-center">
              <ListTodo className="w-10 h-10 text-slate-500 mb-3" />
              <p>Select a task from the list to inspect its execution plan and audit log.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
