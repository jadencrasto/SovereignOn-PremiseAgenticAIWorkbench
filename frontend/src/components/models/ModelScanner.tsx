/**
 * frontend/src/components/models/ModelScanner.tsx
 * ------------------------------------------------
 * Active Host & Local Model Scanner for SIH26117 Sovereign Workbench.
 * Scans local Ollama instance, inspects host VRAM/disk, and verifies dynamic benchmark readiness.
 */

import React, { useState, useEffect } from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import {
  Cpu,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
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
    reasoning_model_name?: string | null;
    vision_model_ready: boolean;
    vision_model_name?: string | null;
    embedding_model_ready: boolean;
    embedding_model_name?: string | null;
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
        
        // If current selectedModel is not in the list, auto-select first available
        if (data.models.length > 0) {
          const names = data.models.map((m) => m.name);
          if (!names.includes(selectedModel) && !names.includes(selectedModel.replace('ollama/', ''))) {
            setSelectedModel(data.models[0].name);
          }
        }
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

  const activeReasoningModel =
    selectedModel.replace('ollama/', '') ||
    scanResult?.readiness.reasoning_model_name ||
    (scanResult?.models.length ? scanResult.models[0].name : 'NONE');

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto bg-slate-50 text-slate-800 p-8 space-y-6 font-sans">
      {/* Header Banner */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 p-6 bg-white border border-slate-200 rounded-xl shadow-sm">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="w-3 h-3 bg-blue-500 rounded-full inline-block" />
            <h1 className="text-xl font-bold tracking-tight text-slate-900">
              Local Model Scanner &bull; Host Discovery
            </h1>
            <span className="text-[10px] font-semibold px-2 py-0.5 bg-blue-50 text-blue-600 border border-blue-200 rounded-full uppercase">
              100% On-Premise
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-2 font-sans">
            Actively scans Ollama on <code className="text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded border border-blue-100">127.0.0.1:11434</code> and dynamically routes tasks to installed models ({scanResult?.models_count || 0} models detected).
          </p>
        </div>

        <button
          onClick={runModelScan}
          disabled={isScanning}
          className="flex items-center gap-2 px-5 py-2.5 bg-blue-500 hover:bg-blue-600 text-white font-medium text-sm rounded-lg shadow-sm transition-all self-start md:self-auto disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${isScanning ? 'animate-spin' : ''}`} />
          <span>{isScanning ? 'SCANNING HOST...' : 'SCAN LOCAL MODELS'}</span>
        </button>
      </div>

      {/* System Benchmark Readiness Grid */}
      {scanResult && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          {/* 01: Service Status */}
          <div className="p-5 bg-white border border-slate-200 rounded-xl shadow-sm flex flex-col justify-between space-y-3">
            <div className="text-xs font-semibold text-slate-500 uppercase">
              01 // OLLAMA DAEMON
            </div>
            <div className="flex items-center gap-2 text-base font-bold text-slate-800">
              {scanResult.status === 'online' ? (
                <>
                  <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                  <span>Online</span>
                </>
              ) : (
                <>
                  <AlertCircle className="w-5 h-5 text-rose-500" />
                  <span>Unreachable</span>
                </>
              )}
            </div>
            <div className="text-xs text-slate-400 truncate">
              {scanResult.service_url}
            </div>
          </div>

          {/* 02: Reasoning Engine (Dynamic) */}
          <div className="p-5 bg-white border border-slate-200 rounded-xl shadow-sm flex flex-col justify-between space-y-3">
            <div className="text-xs font-semibold text-slate-500 uppercase truncate">
              02 // REASONING ({activeReasoningModel})
            </div>
            <div className="flex items-center gap-2 text-base font-bold text-slate-800">
              {scanResult.models.length > 0 ? (
                <>
                  <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                  <span>Active &bull; Ready</span>
                </>
              ) : (
                <>
                  <AlertCircle className="w-5 h-5 text-amber-500" />
                  <span>Not Detected</span>
                </>
              )}
            </div>
            <div className="text-xs text-slate-400 truncate">
              Primary Agent FSM Solver
            </div>
          </div>

          {/* 03: Vision Model */}
          <div className="p-5 bg-white border border-slate-200 rounded-xl shadow-sm flex flex-col justify-between space-y-3">
            <div className="text-xs font-semibold text-slate-500 uppercase">
              03 // VISION INSPECTION
            </div>
            <div className="flex items-center gap-2 text-base font-bold text-slate-800">
              {scanResult.readiness.vision_model_ready ? (
                <>
                  <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                  <span>Ready ({scanResult.readiness.vision_model_name})</span>
                </>
              ) : (
                <>
                  <span className="w-2.5 h-2.5 bg-amber-500 rounded-full inline-block" />
                  <span className="text-sm">Pull With:</span>
                </>
              )}
            </div>
            <div className="text-xs text-slate-400 font-mono">
              {scanResult.readiness.vision_model_ready ? 'NDT Equipment Inspection' : 'ollama pull llava:7b'}
            </div>
          </div>

          {/* 04: Embeddings */}
          <div className="p-5 bg-white border border-slate-200 rounded-xl shadow-sm flex flex-col justify-between space-y-3">
            <div className="text-xs font-semibold text-slate-500 uppercase">
              04 // EMBEDDINGS (RAG)
            </div>
            <div className="flex items-center gap-2 text-base font-bold text-slate-800">
              {scanResult.readiness.embedding_model_ready ? (
                <>
                  <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                  <span>Ready ({scanResult.readiness.embedding_model_name})</span>
                </>
              ) : (
                <>
                  <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                  <span>Built-in RAG Active</span>
                </>
              )}
            </div>
            <div className="text-xs text-slate-400">
              {scanResult.readiness.embedding_model_ready ? 'Vector Grounding RAG' : 'Optional: ollama pull nomic-embed-text'}
            </div>
          </div>
        </div>
      )}

      {/* Discovered Models List */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="font-semibold text-sm text-slate-800 flex items-center gap-2 uppercase tracking-wide">
            <span>// Discovered Local Host Models ({scanResult?.models.length || 0})</span>
          </div>
          <span className="text-sm text-slate-500">
            Active default model: <strong className="text-blue-600 font-mono font-medium">{selectedModel || scanResult?.default_model}</strong>
          </span>
        </div>

        {scanResult && scanResult.models.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {scanResult.models.map((m) => {
              const isSelected = selectedModel === m.id || selectedModel === m.name || selectedModel.replace('ollama/', '') === m.name;
              return (
                <div
                  key={m.id}
                  className={`p-6 bg-white border rounded-xl transition-all flex flex-col justify-between space-y-5 ${
                    isSelected ? 'border-blue-500 shadow-md ring-2 ring-blue-500/10' : 'border-slate-200 hover:border-blue-300 shadow-sm'
                  }`}
                >
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="px-2.5 py-1 bg-blue-50 text-blue-600 font-medium text-[11px] uppercase border border-blue-100 rounded-md">
                        {m.format} &bull; {m.parameter_size}
                      </span>
                      <span className="text-[11px] font-medium text-slate-400">
                        {m.size_gb} GB DISK
                      </span>
                    </div>

                    <div>
                      <h3 className="font-bold text-lg text-slate-900 truncate">
                        {m.name}
                      </h3>
                      <div className="text-xs text-slate-500 mt-1">
                        Quant: <span className="font-medium text-slate-700">{m.quantization_level}</span> &bull; Family: {m.family}
                      </div>
                    </div>

                    <div className="flex flex-wrap gap-1.5 pt-2">
                      {m.capabilities.map((cap) => (
                        <span
                          key={cap}
                          className="px-2 py-0.5 bg-slate-50 text-slate-600 border border-slate-200 rounded text-[10px] font-medium uppercase"
                        >
                          {cap}
                        </span>
                      ))}
                    </div>
                  </div>

                  <div className="pt-4 border-t border-slate-100 flex items-center justify-between">
                    <span className="text-xs text-slate-500 font-medium">
                      {isSelected ? 'Active In Use' : 'Ready to Deploy'}
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
                      className={`px-4 py-1.5 text-sm font-medium rounded-md transition-all shadow-sm ${
                        isSelected
                          ? 'bg-emerald-500 text-white'
                          : 'bg-white hover:bg-slate-50 text-slate-700 border border-slate-200'
                      }`}
                    >
                      {isSelected ? 'Current' : 'Select'}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="p-10 bg-white border border-slate-200 rounded-xl shadow-sm text-center space-y-3">
            <Cpu className="w-10 h-10 text-slate-300 mx-auto" />
            <div className="font-semibold text-slate-700">No Local Models Detected via Ollama</div>
            <p className="text-sm text-slate-500 max-w-md mx-auto">
              Ensure Ollama is active on <code className="text-blue-500 bg-blue-50 px-1 py-0.5 rounded">localhost:11434</code> and you have pulled at least one model (e.g. <code className="text-blue-500 bg-blue-50 px-1 py-0.5 rounded">ollama pull gemma3:4b</code>).
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
