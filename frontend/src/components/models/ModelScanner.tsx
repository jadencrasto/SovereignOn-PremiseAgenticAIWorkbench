/**
 * frontend/src/components/models/ModelScanner.tsx
 * ------------------------------------------------
 * Active Host & Local Model Scanner for SIH26117 Sovereign Workbench.
 * Scans local Ollama instance, inspects host VRAM/disk, and verifies benchmark readiness.
 */

import React, { useState, useEffect } from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import {
  Cpu,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  HardDrive,
  Eye,
  Layers,
  Zap,
  Activity,
  ArrowRight,
  ShieldCheck,
} from 'lucide-react';

interface DiscoveredModel {
  name: string;
  id: string;
  size_gb: number;
  parameter_size: string;
  quantization_level: string;
  format: string;
  family: string;
  modified_at: string;
  capabilities: string[];
}

interface ScanResult {
  status: string;
  service_url: string;
  models_count: number;
  models: DiscoveredModel[];
  error?: string | null;
  readiness: {
    reasoning_model_ready: boolean;
    vision_model_ready: boolean;
    embedding_model_ready: boolean;
    all_ready: boolean;
  };
  default_model: string;
}

export const ModelScanner: React.FC = () => {
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [isScanning, setIsScanning] = useState<boolean>(false);
  const { selectedModel, setSelectedModel, addToast } = useWorkbench();

  const runModelScan = async () => {
    setIsScanning(true);
    try {
      const res = await fetch('/api/models/scan');
      if (res.ok) {
        const data: ScanResult = await res.json();
        setScanResult(data);
        addToast('success', `Found ${data.models_count} local model${data.models_count !== 1 ? 's' : ''} on host.`);
      } else {
        addToast('error', 'Failed to scan local model service.');
      }
    } catch (err) {
      addToast('error', 'Error connecting to local Ollama service.');
    } finally {
      setIsScanning(false);
    }
  };

  useEffect(() => {
    runModelScan();
  }, []);

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto bg-[#f0f7ff] text-[#0f172a] p-8 space-y-6 font-mono">
      {/* Header Banner */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 p-6 bg-white border-2 border-[#cbd5e1] brutal-shadow-blue">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="w-3 h-3 bg-[#0284c7] inline-block" />
            <h1 className="text-xl font-black font-display tracking-tight text-[#0f172a] uppercase">
              Local Model Scanner &bull; Host Discovery
            </h1>
            <span className="text-[10px] font-bold px-2 py-0.5 bg-[#e0f2fe] text-[#0369a1] border border-[#bae6fd] uppercase">
              100% On-Premise
            </span>
          </div>
          <p className="text-xs text-slate-600 mt-1 font-sans">
            Actively scans Ollama on <code className="text-[#0284c7] bg-[#f0f9ff] px-1 border border-[#bae6fd]">127.0.0.1:11434</code> and evaluates model parameters, quantization, and single-resident VRAM constraints.
          </p>
        </div>

        <button
          onClick={runModelScan}
          disabled={isScanning}
          className="flex items-center gap-2 px-5 py-2.5 bg-[#0284c7] hover:bg-[#0369a1] text-white font-bold text-xs uppercase border-2 border-black brutal-shadow-dark brutal-btn self-start md:self-auto disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${isScanning ? 'animate-spin' : ''}`} />
          <span>{isScanning ? 'SCANNING HOST...' : 'SCAN LOCAL MODELS'}</span>
        </button>
      </div>

      {/* System Benchmark Readiness Grid */}
      {scanResult && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          {/* Service Status */}
          <div className="p-4 bg-white border-2 border-[#cbd5e1] brutal-shadow-sky flex flex-col justify-between space-y-2">
            <div className="text-[10px] font-bold text-slate-500 uppercase">
              01 // OLLAMA DAEMON
            </div>
            <div className="flex items-center gap-2 font-display text-base font-black uppercase text-[#0f172a]">
              {scanResult.status === 'online' ? (
                <>
                  <CheckCircle2 className="w-5 h-5 text-[#059669]" />
                  <span>ONLINE</span>
                </>
              ) : (
                <>
                  <AlertCircle className="w-5 h-5 text-[#e11d48]" />
                  <span>UNREACHABLE</span>
                </>
              )}
            </div>
            <div className="text-[10px] text-slate-500 truncate">
              {scanResult.service_url}
            </div>
          </div>

          {/* Reasoning Model */}
          <div className="p-4 bg-white border-2 border-[#cbd5e1] brutal-shadow-sky flex flex-col justify-between space-y-2">
            <div className="text-[10px] font-bold text-slate-500 uppercase">
              02 // REASONING (QWEN2.5:7B)
            </div>
            <div className="flex items-center gap-2 font-display text-base font-black uppercase text-[#0f172a]">
              {scanResult.readiness.reasoning_model_ready ? (
                <>
                  <CheckCircle2 className="w-5 h-5 text-[#059669]" />
                  <span>INSTALLED</span>
                </>
              ) : (
                <>
                  <AlertCircle className="w-5 h-5 text-[#d97706]" />
                  <span>NOT DETECTED</span>
                </>
              )}
            </div>
            <div className="text-[10px] text-slate-500">
              Primary Agent FSM Solver
            </div>
          </div>

          {/* Vision Model */}
          <div className="p-4 bg-white border-2 border-[#cbd5e1] brutal-shadow-sky flex flex-col justify-between space-y-2">
            <div className="text-[10px] font-bold text-slate-500 uppercase">
              03 // VISION (LLAVA:7B)
            </div>
            <div className="flex items-center gap-2 font-display text-base font-black uppercase text-[#0f172a]">
              {scanResult.readiness.vision_model_ready ? (
                <>
                  <CheckCircle2 className="w-5 h-5 text-[#059669]" />
                  <span>INSTALLED</span>
                </>
              ) : (
                <>
                  <AlertCircle className="w-5 h-5 text-[#d97706]" />
                  <span>NOT DETECTED</span>
                </>
              )}
            </div>
            <div className="text-[10px] text-slate-500">
              NDT Equipment Inspection
            </div>
          </div>

          {/* Embeddings */}
          <div className="p-4 bg-white border-2 border-[#cbd5e1] brutal-shadow-sky flex flex-col justify-between space-y-2">
            <div className="text-[10px] font-bold text-slate-500 uppercase">
              04 // EMBEDDINGS (NOMIC)
            </div>
            <div className="flex items-center gap-2 font-display text-base font-black uppercase text-[#0f172a]">
              {scanResult.readiness.embedding_model_ready ? (
                <>
                  <CheckCircle2 className="w-5 h-5 text-[#059669]" />
                  <span>INSTALLED</span>
                </>
              ) : (
                <>
                  <AlertCircle className="w-5 h-5 text-[#d97706]" />
                  <span>NOT DETECTED</span>
                </>
              )}
            </div>
            <div className="text-[10px] text-slate-500">
              Vector Grounding RAG
            </div>
          </div>
        </div>
      )}

      {/* Discovered Models List */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="font-display font-black text-sm uppercase text-[#0f172a] flex items-center gap-2">
            <span>// DISCOVERED LOCAL HOST MODELS ({scanResult?.models.length || 0})</span>
          </div>
          <span className="text-xs text-slate-600 font-sans">
            Active default model: <strong className="text-[#0284c7] font-mono">{scanResult?.default_model || selectedModel}</strong>
          </span>
        </div>

        {scanResult && scanResult.models.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {scanResult.models.map((m) => {
              const isSelected = selectedModel === m.id || selectedModel === m.name;
              return (
                <div
                  key={m.id}
                  className={`p-5 bg-white border-2 transition-all flex flex-col justify-between space-y-4 ${
                    isSelected ? 'border-[#0284c7] brutal-shadow-blue ring-2 ring-[#0284c7]/20' : 'border-[#cbd5e1] hover:border-[#0284c7]'
                  }`}
                >
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="px-2 py-0.5 bg-[#f0f9ff] text-[#0369a1] font-bold text-[10px] uppercase border border-[#bae6fd]">
                        {m.format.toUpperCase()} &bull; {m.parameter_size}
                      </span>
                      <span className="text-[10px] font-bold text-slate-500">
                        {m.size_gb} GB DISK
                      </span>
                    </div>

                    <div>
                      <h3 className="font-display font-black text-base text-[#0f172a] uppercase truncate">
                        {m.name}
                      </h3>
                      <div className="text-[10px] text-slate-500 mt-0.5">
                        Quant: <span className="font-bold text-[#0f172a]">{m.quantization_level}</span> &bull; Family: {m.family}
                      </div>
                    </div>

                    <div className="flex flex-wrap gap-1 pt-1">
                      {m.capabilities.map((cap) => (
                        <span
                          key={cap}
                          className="px-1.5 py-0.5 bg-[#f1f5f9] text-[#334155] border border-[#cbd5e1] text-[9px] font-bold uppercase"
                        >
                          {cap}
                        </span>
                      ))}
                    </div>
                  </div>

                  <div className="pt-3 border-t-2 border-[#f1f5f9] flex items-center justify-between">
                    <span className="text-[10px] text-slate-500 font-bold">
                      {isSelected ? 'ACTIVE IN USE' : 'READY TO DEPLOY'}
                    </span>
                    <button
                      onClick={async () => {
                        setSelectedModel(m.name);
                        addToast('info', `Deploying ${m.name} to GPU memory...`);
                        try {
                          await fetch('/api/models/preload', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ model: m.name }),
                          });
                          addToast('success', `${m.name} is warm in VRAM. Ready for instant inference.`);
                        } catch {
                          // Ignore
                        }
                      }}
                      className={`px-3 py-1.5 text-xs font-bold uppercase border-2 border-black transition-all ${
                        isSelected
                          ? 'bg-[#059669] text-white'
                          : 'bg-[#ffde59] text-black hover:bg-[#fde047] brutal-shadow-dark brutal-btn'
                      }`}
                    >
                      {isSelected ? 'CURRENT' : 'SELECT'}
                    </button>

                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="p-8 bg-white border-2 border-[#cbd5e1] text-center space-y-3">
            <Cpu className="w-8 h-8 text-slate-400 mx-auto" />
            <div className="font-bold text-sm text-[#0f172a] uppercase">No Local Models Detected via Ollama</div>
            <p className="text-xs text-slate-600 font-sans max-w-md mx-auto">
              Ensure Ollama is active on <code className="text-[#0284c7]">localhost:11434</code> and you have pulled at least one model (e.g. <code className="text-[#0284c7]">ollama pull qwen2.5:7b</code>).
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
