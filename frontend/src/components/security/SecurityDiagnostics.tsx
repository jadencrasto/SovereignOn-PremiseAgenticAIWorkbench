/**
 * frontend/src/components/security/SecurityDiagnostics.tsx
 * ---------------------------------------------------------
 * Enterprise Security Diagnostics and Posture Inspector.
 *
 * Displays deterministic PASS/WARN/FAIL security health checks:
 * - SEC-001: Authentication & Mode enforcement
 * - SEC-002: Default credentials & session rotation
 * - SEC-003: Application-level Egress & air-gap validation
 * - SEC-004: Filesystem sandbox containment
 * - SEC-005: CORS origin boundaries
 * - SEC-006: Database journal hardening
 */

import React, { useState, useEffect } from 'react';
import type { SecurityStatusResponse } from '../../types';
import { runSecurityScanApi } from '../../api/security';
import { Lock, RefreshCw, Shield, AlertTriangle, CheckCircle2, XCircle, Clock, Database, Globe, FolderLock, KeyRound } from 'lucide-react';

export const SecurityDiagnostics: React.FC = () => {
  const [data, setData] = useState<SecurityStatusResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [lastScanTime, setLastScanTime] = useState<string | null>(null);

  const loadStatus = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await runSecurityScanApi();
      setData(res);
      const now = new Date();
      setLastScanTime(now.toLocaleTimeString());
    } catch (err: any) {
      setError(err.message || 'Failed to execute security diagnostics scan');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadStatus();
  }, []);

  const getStatusBadge = (status: 'PASS' | 'WARN' | 'FAIL') => {
    switch (status) {
      case 'PASS':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200 shadow-sm font-mono">
            <CheckCircle2 className="w-3 h-3 text-emerald-600" />
            PASS
          </span>
        );
      case 'WARN':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-bold bg-amber-50 text-amber-700 border border-amber-200 shadow-sm font-mono">
            <AlertTriangle className="w-3 h-3 text-amber-600" />
            WARN
          </span>
        );
      case 'FAIL':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-bold bg-red-50 text-red-700 border border-red-200 shadow-sm font-mono">
            <XCircle className="w-3 h-3 text-red-600" />
            FAIL
          </span>
        );
    }
  };

  const getCategoryIcon = (category: string) => {
    const c = category.toLowerCase();
    if (c.includes('auth')) return <KeyRound className="w-3.5 h-3.5 text-amber-400" />;
    if (c.includes('egress') || c.includes('air-gap')) return <Globe className="w-3.5 h-3.5 text-blue-400" />;
    if (c.includes('sandbox') || c.includes('filesystem')) return <FolderLock className="w-3.5 h-3.5 text-purple-400" />;
    if (c.includes('database') || c.includes('journal')) return <Database className="w-3.5 h-3.5 text-emerald-400" />;
    return <Shield className="w-3.5 h-3.5 text-slate-400" />;
  };

  const passCount = data ? data.diagnostics.filter((d) => d.status === 'PASS').length : 0;
  const warnCount = data ? data.diagnostics.filter((d) => d.status === 'WARN').length : 0;
  const failCount = data ? data.diagnostics.filter((d) => d.status === 'FAIL').length : 0;
  const totalCount = data ? data.diagnostics.length : 0;

  return (
    <div className="flex-1 flex flex-col h-full overflow-auto bg-white text-slate-900 p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-900 flex items-center gap-2">
            <Lock className="w-5 h-5 text-blue-600" />
            Security Diagnostics & Posture
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            Automated verification of local sovereign configuration, authentication boundaries, and air-gap posture.
          </p>
        </div>

        <div className="flex items-center gap-3">
          {lastScanTime && (
            <div className="hidden sm:flex items-center gap-1.5 text-xs font-mono text-slate-500 bg-slate-50 px-2.5 py-1 rounded-lg border border-slate-200">
              <Clock className="w-3.5 h-3.5 text-slate-400" />
              <span>Last scan: {lastScanTime}</span>
            </div>
          )}

          <button
            onClick={loadStatus}
            disabled={loading}
            className="flex items-center gap-2 px-3.5 py-1.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-xs font-mono text-slate-600 transition-colors disabled:opacity-50 shadow-sm"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-blue-600' : 'text-slate-500'}`} />
            <span>{loading ? 'Running Security Scan...' : 'Run Security Scan'}</span>
          </button>
        </div>
      </div>

      {loading && !data ? (
        <div className="flex-1 flex flex-col items-center justify-center text-slate-500 text-sm gap-2 py-16">
          <RefreshCw className="w-6 h-6 animate-spin text-blue-600" />
          <span>Evaluating local sovereign security baseline...</span>
        </div>
      ) : error ? (
        <div className="p-4 bg-red-50 border border-red-200 rounded-xl text-red-600 text-sm space-y-2">
          <div className="font-semibold flex items-center gap-2">
            <XCircle className="w-4 h-4 text-red-500" />
            <span>Security Scan Failed</span>
          </div>
          <p className="text-xs text-red-600">{error}</p>
          <button
            onClick={loadStatus}
            className="mt-2 px-3 py-1 bg-white border border-red-200 hover:bg-red-50 rounded text-xs font-mono text-red-700 transition"
          >
            Retry Scan
          </button>
        </div>
      ) : data ? (
        <div className="space-y-5 max-w-4xl">
          {/* Summary Metrics Row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="p-3.5 rounded-xl border border-slate-200 bg-white shadow-sm space-y-1">
              <div className="flex items-center justify-between text-xs text-slate-500 font-mono">
                <span>Total Checks</span>
                <Shield className="w-3.5 h-3.5 text-slate-400" />
              </div>
              <div className="text-2xl font-bold text-slate-900 font-mono">{totalCount}</div>
              <div className="text-[11px] text-slate-500 font-mono">Local Baseline</div>
            </div>

            <div className="p-3.5 rounded-xl border border-slate-200 bg-white shadow-sm space-y-1">
              <div className="flex items-center justify-between text-xs text-slate-500 font-mono">
                <span>Passed</span>
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
              </div>
              <div className="text-2xl font-bold text-emerald-600 font-mono">{passCount}</div>
              <div className="text-[11px] text-slate-500 font-mono">Verified Safe</div>
            </div>

            <div className="p-3.5 rounded-xl border border-slate-200 bg-white shadow-sm space-y-1">
              <div className="flex items-center justify-between text-xs text-slate-500 font-mono">
                <span>Warnings</span>
                <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />
              </div>
              <div className="text-2xl font-bold text-amber-600 font-mono">{warnCount}</div>
              <div className="text-[11px] text-slate-500 font-mono">Dev / Config</div>
            </div>

            <div className="p-3.5 rounded-xl border border-slate-200 bg-white shadow-sm space-y-1">
              <div className="flex items-center justify-between text-xs text-slate-500 font-mono">
                <span>Failures</span>
                <XCircle className="w-3.5 h-3.5 text-red-500" />
              </div>
              <div className="text-2xl font-bold text-red-600 font-mono">{failCount}</div>
              <div className="text-[11px] text-slate-500 font-mono">Action Required</div>
            </div>
          </div>

          {/* Overall Status Banner */}
          <div className="bg-slate-50 border border-slate-200 shadow-sm rounded-xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="text-sm font-semibold text-slate-800">Overall Security Posture:</div>
              {getStatusBadge(data.overall_status)}
            </div>
            <div className="text-xs font-mono text-slate-600">
              <span className="text-blue-600 font-bold">{passCount}</span> of <span className="text-slate-900 font-bold">{totalCount}</span> checks verified compliant
            </div>
          </div>

          {/* Diagnostic Checks List */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-xs font-bold uppercase tracking-wider text-slate-600 font-mono">
                Deterministic Security Baseline Evaluations ({data.diagnostics.length})
              </h2>
              {loading && (
                <span className="text-xs font-mono text-blue-600 animate-pulse flex items-center gap-1">
                  <RefreshCw className="w-3 h-3 animate-spin" />
                  Updating...
                </span>
              )}
            </div>

            {data.diagnostics.map((diag) => (
              <div
                key={diag.id}
                className="bg-white border border-slate-200 rounded-xl p-4 transition hover:border-slate-300 shadow-sm space-y-2"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1.5 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-slate-500 font-bold bg-slate-50 px-2 py-0.5 rounded border border-slate-200">
                        {diag.id}
                      </span>
                      <span className="text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-600 font-medium flex items-center gap-1 border border-slate-200">
                        {getCategoryIcon(diag.category)}
                        {diag.category}
                      </span>
                      <h3 className="font-semibold text-sm text-slate-800">{diag.title}</h3>
                    </div>

                    <p className="text-xs text-slate-600 leading-relaxed font-sans">{diag.details}</p>

                    {diag.remediation && (
                      <div className="mt-2 pt-2 border-t border-slate-100 text-xs text-amber-700 font-mono">
                        <span className="font-semibold text-amber-600">Remediation:</span> {diag.remediation}
                      </div>
                    )}
                  </div>

                  <div className="shrink-0">{getStatusBadge(diag.status)}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
};

