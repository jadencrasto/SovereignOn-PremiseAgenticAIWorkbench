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
import { Activity, RefreshCw } from 'lucide-react';

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
    <div className="flex-1 flex flex-col h-full overflow-auto bg-[#090d16] text-slate-100 p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            <Activity className="w-5 h-5 text-emerald-400" />
            System Health &amp; Observability
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Real-time dependency readiness checks (polled safely without model inference overhead).
          </p>
        </div>

        <div className="flex items-center gap-3">
          {lastChecked && (
            <span className="text-[11px] text-slate-500 font-mono">Updated: {lastChecked}</span>
          )}
          <button
            onClick={loadHealth}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-900 hover:bg-slate-800 text-xs font-mono text-slate-300 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-emerald-400' : ''}`} />
            <span>Check Now</span>
          </button>
        </div>
      </div>

      {loading && !data ? (
        <div className="flex-1 flex items-center justify-center text-slate-400 text-xs font-mono gap-2 py-16">
          <RefreshCw className="w-4 h-4 animate-spin text-emerald-400" />
          <span>Evaluating local dependency readiness...</span>
        </div>
      ) : error ? (
        <div className="p-4 bg-rose-950/40 border border-rose-800/60 rounded-xl text-rose-300 text-xs font-mono">
          {error}
        </div>
      ) : data ? (
        <div className="space-y-4 max-w-4xl">
          {/* Status summary card */}
          <div className="bg-[#0d1424]/70 border border-slate-800 rounded-xl p-4 flex items-center justify-between shadow-md">
            <div className="flex items-center gap-3">
              {getStatusDot(data.status)}
              <span className="text-sm font-semibold uppercase tracking-wider text-slate-200 font-mono">
                System Status: {data.status}
              </span>
            </div>
            {data.cached && (
              <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-400">
                Cached (5s TTL)
              </span>
            )}
          </div>

          {/* Component cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {data.components.map((comp) => (
              <div
                key={comp.name}
                className="bg-[#0d1424]/60 border border-slate-800 rounded-xl p-4 space-y-2 hover:border-slate-700 transition"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {getStatusDot(comp.status)}
                    <h3 className="font-semibold text-sm font-mono text-slate-200">{comp.name}</h3>
                  </div>
                  {comp.latency_ms !== null && comp.latency_ms !== undefined && (
                    <span className="text-[11px] font-mono text-slate-400">
                      {comp.latency_ms.toFixed(1)} ms
                    </span>
                  )}
                </div>
                <p className="text-xs text-slate-400 leading-relaxed font-sans">{comp.details}</p>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
};
