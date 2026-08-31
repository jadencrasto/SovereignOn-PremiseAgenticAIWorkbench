import React, { useState, useEffect } from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import { Database, Cpu, Shield, RefreshCw, Activity, Layers, HardDrive } from 'lucide-react';

interface HardwareTelemetry {
  status: string;
  cpu_percent: number;
  ram_total_mb: number;
  ram_used_mb: number;
  ram_percent: number;
  gpu_available: boolean;
  gpu_name: string;
  gpu_vram_total_mb: number;
  gpu_vram_used_mb: number;
  gpu_vram_free_mb: number;
  gpu_utilization_pct: number;
  telemetry_source: string;
  active_loaded_models: string[];
  last_allocation_decision?: {
    model: string;
    target_device: string;
    reason: string;
  } | null;
}

export const StatusBar: React.FC = () => {
  const {
    isBackendConnected,
    isCheckingHealth,
    refreshHealth,
    health,
    selectedModel,
    documents,
  } = useWorkbench();

  const [hw, setHw] = useState<HardwareTelemetry | null>(null);

  const fetchHardware = async () => {
    try {
      const res = await fetch('/api/hardware/status');
      if (res.ok) {
        const data = await res.json();
        setHw(data);
      }
    } catch {}
  };

  useEffect(() => {
    fetchHardware();
    const timer = setInterval(fetchHardware, 4000);
    return () => clearInterval(timer);
  }, []);

  return (
    <footer className="h-8 bg-[#090e17] border-t border-slate-800/80 px-4 flex items-center justify-between text-[11px] font-mono text-slate-400 select-none shrink-0 overflow-x-auto">
      {/* Left Indicators */}
      <div className="flex items-center gap-4 shrink-0">
        {/* Backend Connectivity */}
        <div className="flex items-center gap-1.5">
          <span
            className={`w-2 h-2 rounded-full ${
              isBackendConnected ? 'bg-emerald-500 shadow-sm shadow-emerald-500/50 animate-pulse' : 'bg-rose-500'
            }`}
          />
          <span className={isBackendConnected ? 'text-emerald-400 font-medium' : 'text-rose-400 font-medium'}>
            {isBackendConnected ? 'SOVEREIGN LOCAL' : 'BACKEND OFFLINE'}
          </span>
          <button
            onClick={() => {
              refreshHealth();
              fetchHardware();
            }}
            title="Refresh status"
            className="hover:text-slate-200 transition-colors ml-0.5"
            disabled={isCheckingHealth}
          >
            <RefreshCw className={`w-3 h-3 ${isCheckingHealth ? 'animate-spin text-emerald-400' : ''}`} />
          </button>
        </div>

        <span className="text-slate-700">|</span>

        {/* Live GPU VRAM Telemetry */}
        {hw && hw.gpu_available ? (
          <div className="flex items-center gap-1.5" title={`${hw.gpu_name} (${hw.telemetry_source})`}>
            <Activity className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-slate-400">VRAM:</span>
            <span className="text-emerald-400 font-semibold">
              {(hw.gpu_vram_used_mb / 1024).toFixed(1)} / {(hw.gpu_vram_total_mb / 1024).toFixed(1)} GB
            </span>
            <span className="text-slate-500">({hw.gpu_utilization_pct.toFixed(0)}% GPU)</span>
          </div>
        ) : hw ? (
          <div className="flex items-center gap-1.5" title="Running in CPU Host mode">
            <Activity className="w-3.5 h-3.5 text-blue-400" />
            <span className="text-slate-400">RAM:</span>
            <span className="text-slate-200">
              {(hw.ram_used_mb / 1024).toFixed(1)} / {(hw.ram_total_mb / 1024).toFixed(1)} GB ({hw.ram_percent}%)
            </span>
          </div>
        ) : null}

        <span className="text-slate-700">|</span>

        {/* Model & Eviction State */}
        <div className="flex items-center gap-1.5">
          <Cpu className="w-3.5 h-3.5 text-blue-400" />
          <span className="text-slate-400">Active Model:</span>
          <span className="text-slate-200 font-medium">
            {hw?.active_loaded_models && hw.active_loaded_models.length > 0
              ? hw.active_loaded_models[0]
              : selectedModel || health?.default_model || 'qwen2.5:7b'}
          </span>
          {hw?.last_allocation_decision && (
            <span
              className="text-[10px] text-slate-500 truncate max-w-[180px]"
              title={hw.last_allocation_decision.reason}
            >
              ({hw.last_allocation_decision.reason})
            </span>
          )}
        </div>

        <span className="text-slate-700">|</span>

        {/* RAG Status */}
        <div className="flex items-center gap-1.5">
          <Database className="w-3.5 h-3.5 text-purple-400" />
          <span className="text-slate-400">RAG Knowledge:</span>
          <span className="text-emerald-400 font-medium">
            {documents.length > 0 ? `${documents.length} Docs Indexed` : 'Standby'}
          </span>
        </div>
      </div>

      {/* Right Indicators */}
      <div className="flex items-center gap-3 shrink-0 ml-4">
        <div className="flex items-center gap-1 text-slate-400">
          <Shield className="w-3 h-3 text-emerald-400" />
          <span>Zero Cloud Egress</span>
        </div>
        <span className="text-slate-700">|</span>
        <span className="text-slate-400">v{health?.version || '0.1.0'}</span>
      </div>
    </footer>
  );
};
