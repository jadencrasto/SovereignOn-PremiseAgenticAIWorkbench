/**
 * frontend/src/components/layout/StatusBar.tsx
 * --------------------------------------------
 * Industrial Hardware Readout Strip (White & Light Blue Style)
 */

import React, { useState, useEffect } from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import { RefreshCw } from 'lucide-react';

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
    <footer className="h-10 bg-white border-t-2 border-[#cbd5e1] px-5 flex items-center justify-between text-[11px] font-mono select-none shrink-0 overflow-x-auto text-[#0f172a]">
      {/* Left Block */}
      <div className="flex items-center gap-2.5 shrink-0 font-bold">
        {/* Core Sovereignty Tag */}
        <div className="flex items-center gap-1.5 px-2.5 py-1 bg-[#f0f9ff] border border-[#bae6fd]">
          <span className={`w-2 h-2 ${isBackendConnected ? 'bg-[#059669]' : 'bg-[#e11d48]'}`} />
          <span className={isBackendConnected ? 'text-[#0369a1]' : 'text-[#be123c]'}>
            {isBackendConnected ? 'SOVEREIGN_AIRGAP' : 'DISCONNECTED'}
          </span>
          <button
            onClick={() => {
              refreshHealth();
              fetchHardware();
            }}
            title="Refresh hardware readout"
            className="ml-1 text-slate-400 hover:text-[#0284c7]"
            disabled={isCheckingHealth}
          >
            <RefreshCw className={`w-3 h-3 ${isCheckingHealth ? 'animate-spin text-[#0284c7]' : ''}`} />
          </button>
        </div>

        {/* Hardware VRAM readout */}
        {hw && hw.gpu_available ? (
          <div className="flex items-center gap-1.5 px-2.5 py-1 bg-[#f8fafc] border border-[#cbd5e1] text-slate-700">
            <span className="text-slate-500">VRAM:</span>
            <span className="text-[#0284c7]">
              {(hw.gpu_vram_used_mb / 1024).toFixed(1)}/{(hw.gpu_vram_total_mb / 1024).toFixed(1)}GB
            </span>
            <span className="text-slate-500">({hw.gpu_utilization_pct.toFixed(0)}% GPU)</span>
          </div>
        ) : hw ? (
          <div className="flex items-center gap-1.5 px-2.5 py-1 bg-[#f8fafc] border border-[#cbd5e1] text-slate-700">
            <span className="text-slate-500">RAM:</span>
            <span className="text-[#0f172a] font-bold">
              {(hw.ram_used_mb / 1024).toFixed(1)}/{(hw.ram_total_mb / 1024).toFixed(1)}GB
            </span>
          </div>
        ) : null}

        {/* Active Model */}
        <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 bg-[#f8fafc] border border-[#cbd5e1] text-slate-700">
          <span className="text-slate-500">ACTIVE_MODEL:</span>
          <span className="text-[#2563eb] uppercase">
            {hw?.active_loaded_models && hw.active_loaded_models.length > 0
              ? hw.active_loaded_models[0]
              : selectedModel || health?.default_model || 'QWEN2.5:7B'}
          </span>
        </div>

        {/* Local Vector Chunks */}
        <div className="hidden md:flex items-center gap-1.5 px-2.5 py-1 bg-[#f8fafc] border border-[#cbd5e1] text-slate-700">
          <span className="text-slate-500">KB_INDEX:</span>
          <span className="text-[#059669]">
            {documents.length > 0 ? `${documents.length} REPO_DOCS` : 'EMPTY'}
          </span>
        </div>
      </div>

      {/* Right Block */}
      <div className="flex items-center gap-2 shrink-0 font-bold text-[10px]">
        <div className="px-2 py-0.5 bg-[#0284c7] text-white border border-black uppercase">
          NO_CLOUD_LEAK &bull; SHA256_VERIFIED
        </div>
      </div>
    </footer>
  );
};
