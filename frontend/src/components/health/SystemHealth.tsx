/**
 * frontend/src/components/health/SystemHealth.tsx
 * ------------------------------------------------
 * System Health & Dependency Readiness Dashboard.
 *
 * Displays live probe metrics for:
 * - SQLite Database WAL mode & writability
 * - Sandbox filesystem accessibility
 * - ChromaDB vector store
 * - Ollama provider and dynamically verified models
 */

import React, { useState, useEffect } from 'react';
import type { ReadinessResponse } from '../../types';
import { fetchSystemReadinessApi } from '../../api/health';

export const SystemHealth: React.FC = () => {
  const [data, setData] = useState<ReadinessResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<string>('');

  const loadHealth = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchSystemReadinessApi();
      setData(res);
      setLastChecked(new Date().toLocaleTimeString());
    } catch (err: any) {
      setError(err.message || 'Failed to query system health');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadHealth();
    const timer = setInterval(loadHealth, 10000); // 10s auto-refresh
    return () => clearInterval(timer);
  }, []);

  const getStatusDot = (status: string) => {
    switch (status) {
      case 'healthy':
        return <div className="w-2.5 h-2.5 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.7)]" />;
      case 'degraded':
        return <div className="w-2.5 h-2.5 rounded-full bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.7)]" />;
      default:
        return <div className="w-2.5 h-2.5 rounded-full bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.7)]" />;
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full overflow-auto bg-zinc-950 text-zinc-100 p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-zinc-100 flex items-center gap-2">
            <span>🩺</span> System Health & Observability
          </h1>
          <p className="text-xs text-zinc-400 mt-1">
            Real-time dependency readiness checks (polled safely without model inference overhead).
          </p>
        </div>

        <div className="flex items-center gap-3">
          {lastChecked && (
            <span className="text-[11px] text-zinc-500">Updated: {lastChecked}</span>
          )}
          <button
            onClick={loadHealth}
            className="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-xs font-medium text-zinc-200 rounded-lg border border-zinc-700 transition"
          >
            Check Now
          </button>
        </div>
      </div>

      {loading && !data ? (
        <div className="flex-1 flex items-center justify-center text-zinc-500 text-sm">
          Checking dependencies...
        </div>
      ) : error ? (
        <div className="p-4 bg-red-950/40 border border-red-800/60 rounded-xl text-red-300 text-sm">
          {error}
        </div>
      ) : data ? (
        <div className="space-y-4 max-w-4xl">
          {/* Status summary card */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 flex items-center justify-between">
            <div className="flex items-center gap-3">
              {getStatusDot(data.status)}
              <span className="text-sm font-semibold uppercase tracking-wider text-zinc-200">
                System Status: {data.status}
              </span>
            </div>
            {data.cached && (
              <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-zinc-800 text-zinc-400">
                Cached (5s TTL)
              </span>
            )}
          </div>

          {/* Component cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {data.components.map((comp) => (
              <div
                key={comp.name}
                className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 space-y-2"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {getStatusDot(comp.status)}
                    <h3 className="font-semibold text-sm font-mono text-zinc-200">{comp.name}</h3>
                  </div>
                  {comp.latency_ms !== null && comp.latency_ms !== undefined && (
                    <span className="text-[11px] font-mono text-zinc-400">
                      {comp.latency_ms.toFixed(1)} ms
                    </span>
                  )}
                </div>
                <p className="text-xs text-zinc-400 leading-relaxed">{comp.details}</p>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
};
