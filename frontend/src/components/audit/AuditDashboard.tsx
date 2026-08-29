/**
 * frontend/src/components/audit/AuditDashboard.tsx
 * -------------------------------------------------
 * Enterprise Centralized Audit Dashboard.
 *
 * Features:
 * - Summary metric cards (Total Events, Failed Events, Denied Actions, Tool Executions)
 * - Search and filters (event type, tool, success/failure, date)
 * - Paginated events table with metadata inspection and failure reasons
 * - Prune retention trigger for Admins
 */

import React, { useState, useEffect } from 'react';
import type { AuditEvent, AuditSummary } from '../../types';
import { fetchAuditEventsApi, fetchAuditSummaryApi, pruneAuditLogApi } from '../../api/audit';
import { useAuth } from '../../context/AuthContext';

export const AuditDashboard: React.FC = () => {
  const { hasRole } = useAuth();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [summary, setSummary] = useState<AuditSummary | null>(null);
  const [total, setTotal] = useState<number>(0);
  const [page, setPage] = useState<number>(0);
  const [pageSize] = useState<number>(20);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [eventTypeFilter, setEventTypeFilter] = useState<string>('');
  const [toolFilter, setToolFilter] = useState<string>('');
  const [successFilter, setSuccessFilter] = useState<string>('all');
  const [selectedEvent, setSelectedEvent] = useState<AuditEvent | null>(null);

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
          event_type: eventTypeFilter || undefined,
          tool: toolFilter || undefined,
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
  }, [page, eventTypeFilter, toolFilter, successFilter]);

  const handlePrune = async () => {
    if (!window.confirm('Prune audit logs older than retention period?')) return;
    try {
      const res = await pruneAuditLogApi();
      alert(`Pruned ${res.deleted_rows} expired audit records.`);
      loadData();
    } catch (err: any) {
      alert(`Error pruning logs: ${err.message}`);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden bg-zinc-950 text-zinc-100 p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-zinc-100 flex items-center gap-2">
            <span>🛡️</span> Centralized Audit Dashboard
          </h1>
          <p className="text-xs text-zinc-400 mt-1">
            Immutable, centralized ledger of all authentication, tool execution, and agent task events.
          </p>
        </div>

        {hasRole('admin') && (
          <button
            onClick={handlePrune}
            className="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-xs font-medium text-zinc-300 rounded-lg border border-zinc-700 transition"
          >
            Prune Expired Logs
          </button>
        )}
      </div>

      {/* Summary Stat Cards */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
            <div className="text-xs text-zinc-400 uppercase font-medium">Total Events</div>
            <div className="text-2xl font-bold text-zinc-100 mt-1">{summary.total_events}</div>
          </div>
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
            <div className="text-xs text-zinc-400 uppercase font-medium">Tool Executions</div>
            <div className="text-2xl font-bold text-sky-400 mt-1">{summary.tool_executions}</div>
          </div>
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
            <div className="text-xs text-zinc-400 uppercase font-medium">Failed Events</div>
            <div className="text-2xl font-bold text-red-400 mt-1">{summary.failed_events}</div>
          </div>
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
            <div className="text-xs text-zinc-400 uppercase font-medium">Denied Actions</div>
            <div className="text-2xl font-bold text-amber-400 mt-1">{summary.denied_actions}</div>
          </div>
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
            <div className="text-xs text-zinc-400 uppercase font-medium">Auth Failures</div>
            <div className="text-2xl font-bold text-rose-400 mt-1">{summary.auth_failures}</div>
          </div>
        </div>
      )}

      {/* Filters Bar */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-3 flex flex-wrap items-center gap-3 text-xs">
        <div className="flex items-center gap-2">
          <span className="text-zinc-400 font-medium">Event:</span>
          <input
            type="text"
            placeholder="e.g. tool.execution"
            value={eventTypeFilter}
            onChange={(e) => {
              setEventTypeFilter(e.target.value);
              setPage(0);
            }}
            className="px-2.5 py-1.5 bg-zinc-800 border border-zinc-700 rounded-lg text-zinc-200 focus:outline-none focus:border-zinc-500"
          />
        </div>

        <div className="flex items-center gap-2">
          <span className="text-zinc-400 font-medium">Tool:</span>
          <input
            type="text"
            placeholder="e.g. calculator"
            value={toolFilter}
            onChange={(e) => {
              setToolFilter(e.target.value);
              setPage(0);
            }}
            className="px-2.5 py-1.5 bg-zinc-800 border border-zinc-700 rounded-lg text-zinc-200 focus:outline-none focus:border-zinc-500"
          />
        </div>

        <div className="flex items-center gap-2">
          <span className="text-zinc-400 font-medium">Status:</span>
          <select
            value={successFilter}
            onChange={(e) => {
              setSuccessFilter(e.target.value);
              setPage(0);
            }}
            className="px-2.5 py-1.5 bg-zinc-800 border border-zinc-700 rounded-lg text-zinc-200 focus:outline-none focus:border-zinc-500"
          >
            <option value="all">All</option>
            <option value="success">Success Only</option>
            <option value="failure">Failure Only</option>
          </select>
        </div>

        <button
          onClick={loadData}
          className="ml-auto px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg border border-zinc-700"
        >
          Refresh
        </button>
      </div>

      {/* Events Table */}
      <div className="flex-1 min-h-0 bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden flex flex-col">
        {loading ? (
          <div className="flex-1 flex items-center justify-center text-zinc-500 text-sm">
            Loading audit records...
          </div>
        ) : error ? (
          <div className="p-6 text-red-400 text-sm">{error}</div>
        ) : events.length === 0 ? (
          <div className="flex-1 flex items-center justify-center text-zinc-500 text-sm">
            No audit records match the selected criteria.
          </div>
        ) : (
          <div className="flex-1 overflow-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead className="bg-zinc-900/80 sticky top-0 border-b border-zinc-800 text-zinc-400">
                <tr>
                  <th className="py-3 px-4 font-semibold">Timestamp</th>
                  <th className="py-3 px-4 font-semibold">Event Type</th>
                  <th className="py-3 px-4 font-semibold">Tool / Action</th>
                  <th className="py-3 px-4 font-semibold">Status</th>
                  <th className="py-3 px-4 font-semibold">Duration</th>
                  <th className="py-3 px-4 font-semibold">Request ID</th>
                  <th className="py-3 px-4 font-semibold text-right">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/60 font-mono text-[11px]">
                {events.map((evt) => (
                  <tr key={evt.event_id} className="hover:bg-zinc-800/30 transition">
                    <td className="py-2.5 px-4 text-zinc-400 whitespace-nowrap">
                      {new Date(evt.timestamp).toLocaleString()}
                    </td>
                    <td className="py-2.5 px-4 font-semibold text-zinc-200">
                      {evt.event_type}
                    </td>
                    <td className="py-2.5 px-4 text-zinc-300">
                      {evt.tool || evt.action || evt.resource || '-'}
                    </td>
                    <td className="py-2.5 px-4">
                      {evt.success ? (
                        <span className="px-2 py-0.5 rounded text-[10px] uppercase font-bold bg-emerald-950 text-emerald-400 border border-emerald-800/40">
                          SUCCESS
                        </span>
                      ) : (
                        <span className="px-2 py-0.5 rounded text-[10px] uppercase font-bold bg-red-950 text-red-400 border border-red-800/40">
                          FAILED
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 px-4 text-zinc-400">
                      {evt.duration_ms !== null && evt.duration_ms !== undefined
                        ? `${evt.duration_ms.toFixed(1)}ms`
                        : '-'}
                    </td>
                    <td className="py-2.5 px-4 text-zinc-500 font-mono text-[10px]">
                      {evt.request_id || '-'}
                    </td>
                    <td className="py-2.5 px-4 text-right">
                      <button
                        onClick={() => setSelectedEvent(evt)}
                        className="text-sky-400 hover:text-sky-300 underline text-xs"
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
        <div className="p-3 border-t border-zinc-800 flex items-center justify-between text-xs text-zinc-400 bg-zinc-900/50">
          <div>
            Showing {events.length > 0 ? page * pageSize + 1 : 0} to{' '}
            {Math.min((page + 1) * pageSize, total)} of {total} events
          </div>
          <div className="flex gap-2">
            <button
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              className="px-2.5 py-1 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 rounded text-zinc-300 transition"
            >
              Previous
            </button>
            <button
              disabled={(page + 1) * pageSize >= total}
              onClick={() => setPage((p) => p + 1)}
              className="px-2.5 py-1 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 rounded text-zinc-300 transition"
            >
              Next
            </button>
          </div>
        </div>
      </div>

      {/* Metadata Detail Modal */}
      {selectedEvent && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="w-full max-w-2xl bg-zinc-900 border border-zinc-800 rounded-xl shadow-2xl overflow-hidden max-h-[85vh] flex flex-col">
            <div className="flex items-center justify-between px-6 py-4 border-b border-zinc-800 bg-zinc-900/50">
              <h3 className="font-semibold text-sm text-zinc-200">
                Audit Event: <span className="font-mono text-zinc-400">{selectedEvent.event_id}</span>
              </h3>
              <button
                onClick={() => setSelectedEvent(null)}
                className="text-zinc-400 hover:text-zinc-200 text-lg"
              >
                &times;
              </button>
            </div>
            <div className="p-6 overflow-auto space-y-4 text-xs font-mono">
              <div className="grid grid-cols-2 gap-2 bg-zinc-950 p-3 rounded-lg border border-zinc-800">
                <div><span className="text-zinc-500">Event:</span> {selectedEvent.event_type}</div>
                <div><span className="text-zinc-500">Timestamp:</span> {selectedEvent.timestamp}</div>
                <div><span className="text-zinc-500">User ID:</span> {selectedEvent.user_id || 'anonymous'}</div>
                <div><span className="text-zinc-500">Role:</span> {selectedEvent.role || 'none'}</div>
                <div><span className="text-zinc-500">Tool:</span> {selectedEvent.tool || '-'}</div>
                <div><span className="text-zinc-500">Task ID:</span> {selectedEvent.task_id || '-'}</div>
              </div>

              {selectedEvent.failure_reason && (
                <div className="p-3 bg-red-950/40 border border-red-800/40 rounded-lg text-red-300">
                  <div className="font-bold text-[11px] mb-1">Failure Reason:</div>
                  <div>{selectedEvent.failure_reason}</div>
                </div>
              )}

              <div>
                <div className="text-zinc-400 font-semibold mb-1">Sanitized Metadata:</div>
                <pre className="p-3 bg-zinc-950 border border-zinc-800 rounded-lg text-zinc-300 overflow-x-auto text-[11px]">
                  {JSON.stringify(selectedEvent.metadata, null, 2)}
                </pre>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
