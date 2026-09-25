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
    <div className="flex-1 flex flex-col h-full overflow-auto bg-white text-slate-900 p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-900 flex items-center gap-2">
            <Activity className="w-5 h-5 text-blue-600" />
            System Health &amp; Observability
          </h1>
          <p className="text-xs text-slate-500 mt-1">
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
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-xs font-mono text-slate-600 transition-colors disabled:opacity-50 shadow-sm"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-blue-600' : ''}`} />
            <span>Check Now</span>
          </button>
        </div>
      </div>

      {loading && !data ? (
        <div className="flex-1 flex items-center justify-center text-slate-500 text-xs font-mono gap-2 py-16">
          <RefreshCw className="w-4 h-4 animate-spin text-blue-600" />
          <span>Evaluating local dependency readiness...</span>
        </div>
      ) : error ? (
        <div className="p-4 bg-red-50 border border-red-200 rounded-xl text-red-600 text-xs font-mono">
          {error}
        </div>
      ) : data ? (
        <div className="space-y-4 max-w-4xl">
          {/* Status summary card */}
          <div className="bg-white border border-slate-200 rounded-xl p-4 flex items-center justify-between shadow-sm">
            <div className="flex items-center gap-3">
              {getStatusDot(data.status)}
              <span className="text-sm font-semibold uppercase tracking-wider text-slate-700 font-mono">
                System Status: {data.status}
              </span>
            </div>
            {data.cached && (
              <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-slate-100 border border-slate-200 text-slate-500">
                Cached (5s TTL)
              </span>
            )}
          </div>

          {/* Component cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {data.components.map((comp) => (
              <div
                key={comp.name}
                className="bg-white border border-slate-200 rounded-xl p-4 space-y-2 hover:border-slate-300 transition shadow-sm"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {getStatusDot(comp.status)}
                    <h3 className="font-semibold text-sm font-mono text-slate-800">{comp.name}</h3>
                  </div>
                  {comp.latency_ms !== null && comp.latency_ms !== undefined && (
                    <span className="text-[11px] font-mono text-slate-500">
                      {comp.latency_ms.toFixed(1)} ms
                    </span>
                  )}
                </div>
                <p className="text-xs text-slate-600 leading-relaxed font-sans">{comp.details}</p>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
};
