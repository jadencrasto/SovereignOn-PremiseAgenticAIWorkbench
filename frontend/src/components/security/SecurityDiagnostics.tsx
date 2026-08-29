/**
 * frontend/src/components/security/SecurityDiagnostics.tsx
 * ---------------------------------------------------------
 * Enterprise Security Diagnostics and Posture Inspector.
 *
 * Displays deterministic PASS/WARN/FAIL security health checks:
 * - Authentication & Credential rotation
 * - Application-level Egress & Air-gap validation
 * - Filesystem sandbox containment
 * - CORS origin boundaries
 * - Database journal hardening
 */

import React, { useState, useEffect } from 'react';
import type { SecurityStatusResponse } from '../../types';
import { fetchSecurityStatusApi } from '../../api/security';

export const SecurityDiagnostics: React.FC = () => {
  const [data, setData] = useState<SecurityStatusResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchSecurityStatusApi();
      setData(res);
    } catch (err: any) {
      setError(err.message || 'Failed to load security diagnostics');
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
          <span className="px-2.5 py-1 rounded-md text-[11px] font-bold bg-emerald-950/80 text-emerald-400 border border-emerald-800/60 shadow-[0_0_10px_rgba(16,185,129,0.2)]">
            ✓ PASS
          </span>
        );
      case 'WARN':
        return (
          <span className="px-2.5 py-1 rounded-md text-[11px] font-bold bg-amber-950/80 text-amber-400 border border-amber-800/60">
            ⚠️ WARN
          </span>
        );
      case 'FAIL':
        return (
          <span className="px-2.5 py-1 rounded-md text-[11px] font-bold bg-red-950/80 text-red-400 border border-red-800/60 shadow-[0_0_10px_rgba(239,68,68,0.2)]">
            ✕ FAIL
          </span>
        );
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full overflow-auto bg-zinc-950 text-zinc-100 p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-zinc-100 flex items-center gap-2">
            <span>🔒</span> Security Diagnostics & Posture
          </h1>
          <p className="text-xs text-zinc-400 mt-1">
            Automated verification of local sovereign configuration, authentication boundaries, and air-gap posture.
          </p>
        </div>

        <button
          onClick={loadStatus}
          className="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-xs font-medium text-zinc-200 rounded-lg border border-zinc-700 transition"
        >
          Run Security Scan
        </button>
      </div>

      {loading ? (
        <div className="flex-1 flex items-center justify-center text-zinc-500 text-sm">
          Evaluating security baseline...
        </div>
      ) : error ? (
        <div className="p-4 bg-red-950/40 border border-red-800/60 rounded-xl text-red-300 text-sm">
          {error}
        </div>
      ) : data ? (
        <div className="space-y-4 max-w-4xl">
          {/* Overall Status Banner */}
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="text-sm font-semibold text-zinc-200">Overall Security Posture:</div>
              {getStatusBadge(data.overall_status)}
            </div>
            <div className="text-xs text-zinc-400">
              {data.diagnostics.filter((d) => d.status === 'PASS').length} / {data.diagnostics.length} checks passed
            </div>
          </div>

          {/* Diagnostic Checks List */}
          <div className="space-y-3">
            {data.diagnostics.map((diag) => (
              <div
                key={diag.id}
                className="bg-zinc-900/90 border border-zinc-800 rounded-xl p-4 transition hover:border-zinc-700"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-zinc-500 font-semibold">{diag.id}</span>
                      <span className="text-xs px-2 py-0.5 rounded bg-zinc-800 text-zinc-400 font-medium">
                        {diag.category}
                      </span>
                      <h3 className="font-semibold text-sm text-zinc-200">{diag.title}</h3>
                    </div>
                    <p className="text-xs text-zinc-400 mt-1 leading-relaxed">{diag.details}</p>

                    {diag.remediation && (
                      <div className="mt-2 pt-2 border-t border-zinc-800/80 text-xs text-amber-300/90">
                        <span className="font-semibold text-amber-400">Remediation:</span> {diag.remediation}
                      </div>
                    )}
                  </div>
                  <div>{getStatusBadge(diag.status)}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
};
