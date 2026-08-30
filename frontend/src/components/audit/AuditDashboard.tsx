/**
 * frontend/src/components/audit/AuditDashboard.tsx
 * -------------------------------------------------
 * Enterprise Centralized Audit Dashboard.
 *
 * Features:
 * - Summary metric cards (Total Events, Tool Executions, Failed Events, Denied Actions, Auth Failures)
 * - Debounced search and filters (event type, tool, success/failure status)
 * - Paginated events table with color-coded status badges and duration metrics
 * - Structured Inspector modal for tool execution, approval, and security audit events
 * - Raw sanitized JSON inspector fallback
 * - Prune retention trigger for Administrators
 */

import React, { useState, useEffect } from 'react';
import type { AuditEvent, AuditSummary } from '../../types';
import { fetchAuditEventsApi, fetchAuditSummaryApi, pruneAuditLogApi } from '../../api/audit';
import { useAuth } from '../../context/AuthContext';
import {
  ShieldAlert,
  RefreshCw,
  CheckCircle2,
  XCircle,
  Wrench,
  ShieldX,
  KeyRound,
  Copy,
  Check,
  X,
  Activity,
} from 'lucide-react';

export const AuditDashboard: React.FC = () => {
  const { hasRole } = useAuth();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [summary, setSummary] = useState<AuditSummary | null>(null);
  const [total, setTotal] = useState<number>(0);
  const [page, setPage] = useState<number>(0);
  const [pageSize] = useState<number>(20);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Filter input states (immediate typing feedback)
  const [eventTypeInput, setEventTypeInput] = useState<string>('');
  const [toolInput, setToolInput] = useState<string>('');
  const [successFilter, setSuccessFilter] = useState<string>('all');

  // Debounced filter states (applied after 250ms)
  const [debouncedEventType, setDebouncedEventType] = useState<string>('');
  const [debouncedTool, setDebouncedTool] = useState<string>('');

  const [selectedEvent, setSelectedEvent] = useState<AuditEvent | null>(null);
  const [copiedRaw, setCopiedRaw] = useState<boolean>(false);

  // 250ms Debounce effect for text filters
  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedEventType(eventTypeInput.trim());
      setDebouncedTool(toolInput.trim());
      setPage(0);
    }, 250);

    return () => clearTimeout(handler);
  }, [eventTypeInput, toolInput]);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const successParam =
        successFilter === 'success' ? true : successFilter === 'failure' ? false : undefined;

      const [eventsRes, summaryRes] = await Promise.all([
        fetchAuditEventsApi({
          limit: pageSize,
          offset: page * pageSize,
          event_type: debouncedEventType || undefined,
          tool: debouncedTool || undefined,
          success: successParam,
        }),
        fetchAuditSummaryApi(),
      ]);

      setEvents(eventsRes.events);
      setTotal(eventsRes.total);
      setSummary(summaryRes);
    } catch (err: any) {
      setError(err.message || 'Failed to load audit data');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [page, debouncedEventType, debouncedTool, successFilter]);

  const handlePrune = async () => {
    if (!window.confirm('Prune audit logs older than configured retention period?')) return;
    try {
      const res = await pruneAuditLogApi();
      alert(`Pruned ${res.deleted_rows} expired audit records.`);
      loadData();
    } catch (err: any) {
      alert(`Error pruning logs: ${err.message}`);
    }
  };

  const handleCopyRaw = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedRaw(true);
    setTimeout(() => setCopiedRaw(false), 2000);
  };

  // Close modal on Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSelectedEvent(null);
    };
    if (selectedEvent) window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedEvent]);

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden bg-[#090d16] text-slate-100 p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            <ShieldAlert className="w-5 h-5 text-amber-400" />
            Audit Log &amp; Event Ledger
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Immutable on-premise ledger recording tool dispatches, human approvals, authentication, and task lifecycles.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={loadData}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-900 hover:bg-slate-800 text-xs font-mono text-slate-300 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-emerald-400' : ''}`} />
            <span>Refresh</span>
          </button>
          {hasRole('admin') && (
            <button
              onClick={handlePrune}
              className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-xs font-medium text-slate-300 rounded-lg border border-slate-700 transition"
            >
              Prune Expired Logs
            </button>
          )}
        </div>
      </div>

      {/* Summary Stat Cards */}
      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          <div className="bg-[#0d1424]/70 border border-slate-800 rounded-xl p-3.5 space-y-1">
            <div className="text-[11px] text-slate-400 uppercase font-mono font-medium flex items-center justify-between">
              <span>Total Events</span>
              <Activity className="w-3.5 h-3.5 text-slate-500" />
            </div>
            <div className="text-2xl font-bold text-white font-mono">{summary.total_events}</div>
            <div className="text-[10.5px] text-slate-500 font-mono">SQLite WAL Ledger</div>
          </div>

          <div className="bg-[#0d1424]/70 border border-slate-800 rounded-xl p-3.5 space-y-1">
            <div className="text-[11px] text-slate-400 uppercase font-mono font-medium flex items-center justify-between">
              <span>Tool Executions</span>
              <Wrench className="w-3.5 h-3.5 text-sky-400" />
            </div>
            <div className="text-2xl font-bold text-sky-400 font-mono">{summary.tool_executions}</div>
            <div className="text-[10.5px] text-slate-500 font-mono">5 Registered Tools</div>
          </div>

          <div className="bg-[#0d1424]/70 border border-slate-800 rounded-xl p-3.5 space-y-1">
            <div className="text-[11px] text-slate-400 uppercase font-mono font-medium flex items-center justify-between">
              <span>Failed Events</span>
              <XCircle className="w-3.5 h-3.5 text-rose-400" />
            </div>
            <div className="text-2xl font-bold text-rose-400 font-mono">{summary.failed_events}</div>
            <div className="text-[10.5px] text-slate-500 font-mono">Handled Cleanly</div>
          </div>

          <div className="bg-[#0d1424]/70 border border-slate-800 rounded-xl p-3.5 space-y-1">
            <div className="text-[11px] text-slate-400 uppercase font-mono font-medium flex items-center justify-between">
              <span>Denied Actions</span>
              <ShieldX className="w-3.5 h-3.5 text-amber-400" />
            </div>
            <div className="text-2xl font-bold text-amber-400 font-mono">{summary.denied_actions}</div>
            <div className="text-[10.5px] text-slate-500 font-mono">Operator Rejections</div>
          </div>

          <div className="bg-[#0d1424]/70 border border-slate-800 rounded-xl p-3.5 space-y-1">
            <div className="text-[11px] text-slate-400 uppercase font-mono font-medium flex items-center justify-between">
              <span>Auth Failures</span>
              <KeyRound className="w-3.5 h-3.5 text-rose-400" />
            </div>
            <div className="text-2xl font-bold text-rose-400 font-mono">{summary.auth_failures}</div>
            <div className="text-[10.5px] text-slate-500 font-mono">Lockout Protected</div>
          </div>
        </div>
      )}

      {/* Filters Bar */}
      <div className="bg-[#0d1424]/60 border border-slate-800 rounded-xl p-3 flex flex-wrap items-center gap-3 text-xs">
        <div className="flex items-center gap-2">
          <span className="text-slate-400 font-mono text-[11px]">Event:</span>
          <div className="relative">
            <input
              type="text"
              placeholder="e.g. tool.execution"
              value={eventTypeInput}
              onChange={(e) => setEventTypeInput(e.target.value)}
              className="px-2.5 py-1.5 bg-[#090d16] border border-slate-700 rounded-lg text-slate-200 focus:outline-none focus:border-emerald-500 font-mono text-xs w-44"
            />
          </div>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-slate-400 font-mono text-[11px]">Tool:</span>
          <input
            type="text"
            placeholder="e.g. file_write"
            value={toolInput}
            onChange={(e) => setToolInput(e.target.value)}
            className="px-2.5 py-1.5 bg-[#090d16] border border-slate-700 rounded-lg text-slate-200 focus:outline-none focus:border-emerald-500 font-mono text-xs w-36"
          />
        </div>

        <div className="flex items-center gap-2">
          <span className="text-slate-400 font-mono text-[11px]">Status:</span>
          <select
            value={successFilter}
            onChange={(e) => {
              setSuccessFilter(e.target.value);
              setPage(0);
            }}
            className="px-2.5 py-1.5 bg-[#090d16] border border-slate-700 rounded-lg text-slate-200 focus:outline-none focus:border-emerald-500 font-mono text-xs cursor-pointer"
          >
            <option value="all">All Statuses</option>
            <option value="success">Success Only</option>
            <option value="failure">Failure Only</option>
          </select>
        </div>

        <div className="ml-auto text-[11px] font-mono text-slate-500">
          Showing {events.length > 0 ? page * pageSize + 1 : 0}–{Math.min((page + 1) * pageSize, total)} of {total} records
        </div>
      </div>

      {/* Events Table */}
      <div className="flex-1 min-h-0 bg-[#0d1424]/60 border border-slate-800 rounded-xl overflow-hidden flex flex-col shadow-md">
        {loading ? (
          <div className="flex-1 flex items-center justify-center text-slate-400 text-xs font-mono gap-2">
            <RefreshCw className="w-4 h-4 animate-spin text-emerald-400" />
            <span>Loading audit ledger...</span>
          </div>
        ) : error ? (
          <div className="p-6 text-rose-400 text-xs font-mono">{error}</div>
        ) : events.length === 0 ? (
          <div className="flex-1 flex items-center justify-center text-slate-500 text-xs font-mono">
            No audit records match the active filter criteria.
          </div>
        ) : (
          <div className="flex-1 overflow-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead className="bg-[#090d16] sticky top-0 border-b border-slate-800 text-slate-400 font-mono text-[11px]">
                <tr>
                  <th className="py-3 px-4 font-medium">Timestamp</th>
                  <th className="py-3 px-4 font-medium">Event Type</th>
                  <th className="py-3 px-4 font-medium">Tool / Action</th>
                  <th className="py-3 px-4 font-medium">Status</th>
                  <th className="py-3 px-4 font-medium">Duration</th>
                  <th className="py-3 px-4 font-medium">Task ID</th>
                  <th className="py-3 px-4 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 font-mono text-[11.5px]">
                {events.map((evt) => (
                  <tr
                    key={evt.event_id}
                    onClick={() => setSelectedEvent(evt)}
                    className="hover:bg-slate-800/40 transition cursor-pointer group"
                    title="Click to inspect audit event metadata"
                  >
                    <td className="py-2.5 px-4 text-slate-400 whitespace-nowrap">
                      {new Date(evt.timestamp).toLocaleTimeString()}
                    </td>
                    <td className="py-2.5 px-4 font-semibold text-slate-200 group-hover:text-emerald-300 transition-colors">
                      {evt.event_type}
                    </td>
                    <td className="py-2.5 px-4 text-slate-300">
                      {evt.tool ? (
                        <span className="inline-flex items-center gap-1 text-sky-400">
                          <Wrench className="w-3 h-3" />
                          {evt.tool}
                        </span>
                      ) : (
                        evt.action || evt.resource || '-'
                      )}
                    </td>
                    <td className="py-2.5 px-4">
                      {evt.success ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] uppercase font-bold bg-emerald-950/80 text-emerald-400 border border-emerald-800/60 font-mono">
                          <CheckCircle2 className="w-2.5 h-2.5" />
                          SUCCESS
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] uppercase font-bold bg-rose-950/80 text-rose-400 border border-rose-800/60 font-mono">
                          <XCircle className="w-2.5 h-2.5" />
                          FAILED
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 px-4 text-slate-400">
                      {evt.duration_ms !== null && evt.duration_ms !== undefined
                        ? `${evt.duration_ms.toFixed(1)}ms`
                        : '-'}
                    </td>
                    <td className="py-2.5 px-4 text-slate-500 truncate max-w-[120px]">
                      {evt.task_id || '-'}
                    </td>
                    <td className="py-2.5 px-4 text-right">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedEvent(evt);
                        }}
                        className="text-emerald-400 hover:text-emerald-300 underline text-xs font-mono"
                      >
                        Inspect
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination Bar */}
        <div className="p-3 border-t border-slate-800 flex items-center justify-between text-xs text-slate-400 bg-[#090d16] font-mono">
          <div>
            Page {page + 1} of {Math.max(1, Math.ceil(total / pageSize))}
          </div>
          <div className="flex gap-2">
            <button
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              className="px-3 py-1 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 rounded text-slate-300 transition text-xs font-mono"
            >
              Previous
            </button>
            <button
              disabled={(page + 1) * pageSize >= total}
              onClick={() => setPage((p) => p + 1)}
              className="px-3 py-1 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 rounded text-slate-300 transition text-xs font-mono"
            >
              Next
            </button>
          </div>
        </div>
      </div>

      {/* Structured Metadata Detail Modal */}
      {selectedEvent && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4"
          onClick={(e) => {
            if (e.target === e.currentTarget) setSelectedEvent(null);
          }}
        >
          <div className="w-full max-w-2xl bg-[#0d1424] border border-slate-800 rounded-2xl shadow-2xl overflow-hidden max-h-[88vh] flex flex-col">
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800 bg-[#090d16]">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-slate-800 border border-slate-700 flex items-center justify-center text-amber-400">
                  <ShieldAlert className="w-4 h-4" />
                </div>
                <div>
                  <h3 className="font-semibold text-sm text-white">
                    Audit Event: <span className="font-mono text-emerald-400">{selectedEvent.event_type}</span>
                  </h3>
                  <p className="text-[11px] font-mono text-slate-500">ID: {selectedEvent.event_id}</p>
                </div>
              </div>
              <button
                onClick={() => setSelectedEvent(null)}
                className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Modal Body */}
            <div className="p-6 overflow-y-auto space-y-4 text-xs font-mono">
              {/* Event Core Details */}
              <div className="grid grid-cols-2 gap-2 bg-[#090d16] p-3.5 rounded-xl border border-slate-800">
                <div><span className="text-slate-500">Event Type:</span> <span className="text-white font-bold">{selectedEvent.event_type}</span></div>
                <div><span className="text-slate-500">Timestamp:</span> <span className="text-slate-300">{new Date(selectedEvent.timestamp).toLocaleString()}</span></div>
                <div><span className="text-slate-500">User:</span> <span className="text-slate-300">{selectedEvent.user_id || 'anonymous'}</span></div>
                <div><span className="text-slate-500">Role:</span> <span className="text-emerald-400 uppercase">{selectedEvent.role || 'none'}</span></div>
                <div><span className="text-slate-500">Tool:</span> <span className="text-sky-400">{selectedEvent.tool || '-'}</span></div>
                <div><span className="text-slate-500">Task ID:</span> <span className="text-slate-300">{selectedEvent.task_id || '-'}</span></div>
                <div><span className="text-slate-500">Duration:</span> <span className="text-slate-300">{selectedEvent.duration_ms !== null && selectedEvent.duration_ms !== undefined ? `${selectedEvent.duration_ms.toFixed(1)} ms` : '-'}</span></div>
                <div>
                  <span className="text-slate-500">Status:</span>{' '}
                  {selectedEvent.success ? (
                    <span className="text-emerald-400 font-bold">SUCCESS</span>
                  ) : (
                    <span className="text-rose-400 font-bold">FAILED</span>
                  )}
                </div>
              </div>

              {/* Failure Alert Banner */}
              {selectedEvent.failure_reason && (
                <div className="p-3.5 bg-rose-950/40 border border-rose-800/60 rounded-xl text-rose-300 space-y-1">
                  <div className="font-bold text-[11px] text-rose-400 flex items-center gap-1.5">
                    <XCircle className="w-3.5 h-3.5" />
                    Failure Reason:
                  </div>
                  <div className="text-xs font-sans text-rose-200">{selectedEvent.failure_reason}</div>
                </div>
              )}

              {/* Structured Metadata Breakdown */}
              {selectedEvent.metadata && typeof selectedEvent.metadata === 'object' && Object.keys(selectedEvent.metadata).length > 0 && (
                <div className="p-3.5 bg-[#090d16] border border-slate-800 rounded-xl space-y-2">
                  <div className="text-slate-400 font-semibold uppercase text-[10.5px] tracking-wider">
                    Structured Event Metadata
                  </div>
                  <div className="space-y-1.5">
                    {Object.entries(selectedEvent.metadata).map(([k, v]) => (
                      <div key={k} className="flex items-start gap-2 border-b border-slate-800/50 pb-1 last:border-0 last:pb-0">
                        <span className="text-slate-500 min-w-[120px] shrink-0 font-medium">{k}:</span>
                        <span className="text-slate-200 break-all font-mono">
                          {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Raw JSON Secondary Inspector */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-slate-400 font-semibold text-[11px]">
                  <span>Raw Sanitized JSON:</span>
                  <button
                    onClick={() => handleCopyRaw(JSON.stringify(selectedEvent, null, 2))}
                    className="flex items-center gap-1 text-[10px] text-emerald-400 hover:text-emerald-300 transition"
                  >
                    {copiedRaw ? (
                      <>
                        <Check className="w-3 h-3" />
                        <span>Copied JSON</span>
                      </>
                    ) : (
                      <>
                        <Copy className="w-3 h-3" />
                        <span>Copy JSON</span>
                      </>
                    )}
                  </button>
                </div>
                <pre className="p-3 bg-[#070a12] border border-slate-800 rounded-xl text-slate-300 overflow-x-auto text-[11px] max-h-48">
                  {JSON.stringify(selectedEvent.metadata || {}, null, 2)}
                </pre>
              </div>
            </div>

            {/* Footer */}
            <div className="px-6 py-3 border-t border-slate-800 bg-[#090d16] flex items-center justify-between text-xs text-slate-500 font-mono">
              <span>On-premise cryptographic ledger record</span>
              <button
                onClick={() => setSelectedEvent(null)}
                className="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-semibold transition"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
