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
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-slate-500/10 text-slate-400 border border-slate-500/20">
            <AlertCircle className="w-3 h-3" /> Cancelled
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-slate-500/10 text-slate-400 border border-slate-500/20">
            <Clock className="w-3 h-3" /> {status}
          </span>
        );
    }
  };

  return (
    <div className="flex h-full flex-col bg-[#090d16] text-slate-100">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-800 px-6 py-4 bg-[#0c121e]/80 backdrop-blur-md">
        <div>
          <h1 className="text-lg font-semibold flex items-center gap-2 text-white tracking-tight">
            <ListTodo className="w-5 h-5 text-sky-400" />
            Agent Task History
          </h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Audit log of autonomous plans, approvals, and persistent execution state.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 bg-[#090d16] border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 font-mono">
            <Filter className="w-3.5 h-3.5 text-slate-500" />
            <select
              className="bg-transparent text-xs focus:outline-none cursor-pointer"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="" className="bg-[#090d16]">All Statuses</option>
              <option value="completed" className="bg-[#090d16]">Completed</option>
              <option value="awaiting_approval" className="bg-[#090d16]">Awaiting Approval</option>
              <option value="executing" className="bg-[#090d16]">Executing</option>
              <option value="failed" className="bg-[#090d16]">Failed</option>
              <option value="cancelled" className="bg-[#090d16]">Cancelled</option>
            </select>
          </div>
          <button
            onClick={loadTasks}
            disabled={isLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-slate-900 hover:bg-slate-800 border border-slate-700 rounded-lg transition-colors text-slate-300 disabled:opacity-50 font-mono"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin text-emerald-400' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Task List */}
        <div className="w-1/2 border-r border-slate-800 overflow-y-auto p-4 space-y-2 bg-[#090d16]">
          {error && (
            <div className="p-3 mb-3 bg-rose-950/40 border border-rose-500/30 rounded-lg text-xs text-rose-300 flex items-center gap-2 font-mono">
              <AlertCircle className="w-4 h-4 shrink-0" />
              {error}
            </div>
          )}

          {isLoading ? (
            <div className="flex flex-col items-center justify-center h-48 text-slate-500 text-xs font-mono">
              <Loader2 className="w-6 h-6 animate-spin text-sky-400 mb-2" />
              Loading persistent tasks...
            </div>
          ) : tasks.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 text-slate-500 text-xs text-center border border-dashed border-slate-800 rounded-lg p-6 bg-[#0d1424]/30">
              <ListTodo className="w-8 h-8 text-slate-600 mb-2" />
              <p>No agent tasks recorded yet.</p>
              <p className="text-[11px] text-slate-600 mt-1">
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
                  className={`p-3.5 rounded-xl border text-left cursor-pointer transition-all ${
                    isSelected
                      ? 'bg-[#0d1726] border-sky-500/50 shadow-md shadow-sky-950/30'
                      : 'bg-[#0d1424]/60 hover:bg-[#0d1424] hover:border-slate-700 border-slate-800/80'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[11px] font-mono text-slate-400 font-medium">
                      {task.task_id}
                    </span>
                    {getStatusBadge(task.status)}
                  </div>
                  <p className="text-xs text-slate-200 font-medium line-clamp-2 mb-2">
                    {task.user_request}
                  </p>
                  <div className="flex items-center justify-between text-[10px] text-slate-500 font-mono">
                    <div className="flex items-center gap-2">
                      <span>{task.completed_steps}/{task.step_count} steps</span>
                      <span>•</span>
                      <span>{new Date(task.created_at).toLocaleTimeString()}</span>
                    </div>
                    <ChevronRight className="w-3.5 h-3.5 text-slate-600" />
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Right: Task Details */}
        <div className="w-1/2 overflow-y-auto p-6 bg-[#0c121e]/50">
          {isLoadingDetail ? (
            <div className="flex flex-col items-center justify-center h-full text-slate-500 text-xs">
              <Loader2 className="w-6 h-6 animate-spin text-sky-400 mb-2" />
              Loading task details...
            </div>
          ) : selectedTask ? (
            <div className="space-y-6">
              <div>
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-slate-400">
                    Task {selectedTask.task_id}
                  </span>
                  {getStatusBadge(selectedTask.status)}
                </div>
                <h2 className="text-sm font-semibold text-slate-100 mt-2">
                  {selectedTask.user_request}
                </h2>
                <div className="flex items-center gap-4 text-xs text-slate-500 mt-2 font-mono">
                  <span>Session: {selectedTask.session_id.slice(0, 8)}...</span>
                  <span>Created: {new Date(selectedTask.created_at).toLocaleTimeString()}</span>
                </div>
              </div>

              {/* Deterministic Lifecycle Stepper */}
              <div className="p-3.5 rounded-xl bg-[#0d1424]/80 border border-slate-800 space-y-1.5 shadow-sm">
                <div className="text-[10px] font-mono uppercase text-slate-400 font-semibold tracking-wider">
                  Deterministic Task Lifecycle
                </div>
                <div className="flex items-center gap-1.5 text-[11px] font-mono overflow-x-auto py-1">
                  <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                    1. Request
                  </span>
                  <span className="text-slate-600">→</span>
                  <span className={`px-2 py-0.5 rounded ${selectedTask.plan ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-slate-800 text-slate-500'}`}>
                    2. Plan
                  </span>
                  <span className="text-slate-600">→</span>
                  <span className={`px-2 py-0.5 rounded ${selectedTask.plan ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-slate-800 text-slate-500'}`}>
                    3. Validation
                  </span>
                  <span className="text-slate-600">→</span>
                  <span className={`px-2 py-0.5 rounded ${selectedTask.plan?.steps?.some((s: any) => s.risk_level === 'high') ? (selectedTask.status === 'awaiting_approval' ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30 animate-pulse' : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30') : 'bg-slate-800 text-slate-500'}`}>
                    4. Approval
                  </span>
                  <span className="text-slate-600">→</span>
                  <span className={`px-2 py-0.5 rounded ${selectedTask.status === 'executing' ? 'bg-sky-500/20 text-sky-300 border border-sky-500/30 animate-pulse' : selectedTask.status === 'completed' ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : selectedTask.status === 'failed' ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30' : 'bg-slate-800 text-slate-500'}`}>
                    5. Execution
                  </span>
                  <span className="text-slate-600">→</span>
                  <span className={`px-2 py-0.5 rounded ${selectedTask.result ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-slate-800 text-slate-500'}`}>
                    6. Result
                  </span>
                  <span className="text-slate-600">→</span>
                  <span className={`px-2 py-0.5 rounded ${selectedTask.status === 'completed' || selectedTask.status === 'failed' ? 'bg-purple-500/20 text-purple-300 border border-purple-500/30' : 'bg-slate-800 text-slate-500'}`}>
                    7. Audit
                  </span>
                </div>
              </div>

              {selectedTask.plan && selectedTask.plan.steps && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-300 mb-2 uppercase tracking-wider font-mono">
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
                  <h3 className="text-xs font-semibold text-slate-300 mb-2 uppercase tracking-wider font-mono">
                    Final Result
                  </h3>
                  <div className="p-3.5 rounded-xl bg-[#0d1424]/80 border border-slate-800 text-xs text-slate-200 whitespace-pre-wrap font-sans leading-relaxed shadow-sm">
                    {selectedTask.result}
                  </div>
                </div>
              )}

              {selectedTask.error && (
                <div>
                  <h3 className="text-xs font-semibold text-rose-400 mb-2 uppercase tracking-wider font-mono">
                    Error Log
                  </h3>
                  <div className="p-3.5 rounded-xl bg-rose-950/30 border border-rose-500/30 text-xs text-rose-300 whitespace-pre-wrap font-mono">
                    {selectedTask.error}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-slate-500 text-xs text-center">
              <ListTodo className="w-10 h-10 text-slate-700 mb-2" />
              <p>Select a task from the list to inspect its execution plan and audit log.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
