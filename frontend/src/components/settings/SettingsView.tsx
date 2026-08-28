import React from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import { Settings, ShieldCheck, Server, HardDrive, Cpu, RefreshCw } from 'lucide-react';
import { Badge } from '../common/Badge';

export const SettingsView: React.FC = () => {
  const {
    health,
    isCheckingHealth,
    refreshHealth,
    defaultModel,
    selectedModel,
    documents,
  } = useWorkbench();

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto bg-[#090d16] p-6">
      <div className="max-w-4xl mx-auto w-full space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
              <Settings className="w-5 h-5 text-slate-400" />
              Sovereign System Configuration
            </h1>
            <p className="text-xs text-slate-400 mt-1 max-w-xl">
              Runtime environment parameters, local storage bindings, and model inference connectivity.
            </p>
          </div>

          <button
            onClick={refreshHealth}
            disabled={isCheckingHealth}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-900 hover:bg-slate-800 text-xs font-mono text-slate-300 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isCheckingHealth ? 'animate-spin text-emerald-400' : ''}`} />
            <span>Health Check</span>
          </button>
        </div>

        {/* Backend & Runtime */}
        <div className="rounded-xl border border-slate-800 bg-[#0d1424]/60 p-5 space-y-4">
          <div className="flex items-center gap-2 pb-3 border-b border-slate-800/80">
            <Server className="w-4 h-4 text-emerald-400" />
            <h2 className="text-sm font-semibold text-white">Backend Service</h2>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 font-mono text-xs">
            <div className="p-3 rounded-lg bg-[#090d16] border border-slate-800 space-y-1">
              <div className="text-slate-500">Service Name:</div>
              <div className="text-slate-200 font-medium">{health?.service || 'sovereign-workbench'}</div>
            </div>

            <div className="p-3 rounded-lg bg-[#090d16] border border-slate-800 space-y-1">
              <div className="text-slate-500">Core Version:</div>
              <div className="text-slate-200 font-medium">v{health?.version || '0.1.0-internal'}</div>
            </div>

            <div className="p-3 rounded-lg bg-[#090d16] border border-slate-800 space-y-1">
              <div className="text-slate-500">Environment:</div>
              <div className="text-emerald-400 font-medium uppercase">{health?.environment || 'development'}</div>
            </div>

            <div className="p-3 rounded-lg bg-[#090d16] border border-slate-800 space-y-1">
              <div className="text-slate-500">Backend API URL:</div>
              <div className="text-slate-200 font-medium">http://localhost:8000</div>
            </div>
          </div>
        </div>

        {/* Model Inference Settings */}
        <div className="rounded-xl border border-slate-800 bg-[#0d1424]/60 p-5 space-y-4">
          <div className="flex items-center gap-2 pb-3 border-b border-slate-800/80">
            <Cpu className="w-4 h-4 text-blue-400" />
            <h2 className="text-sm font-semibold text-white">Local Inference Engine</h2>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 font-mono text-xs">
            <div className="p-3 rounded-lg bg-[#090d16] border border-slate-800 space-y-1">
              <div className="text-slate-500">Ollama Runtime URL:</div>
              <div className="text-slate-200 font-medium">{health?.ollama_url || 'http://localhost:11434'}</div>
            </div>

            <div className="p-3 rounded-lg bg-[#090d16] border border-slate-800 space-y-1">
              <div className="text-slate-500">Configured Default Model:</div>
              <div className="text-slate-200 font-medium">{defaultModel}</div>
            </div>

            <div className="p-3 rounded-lg bg-[#090d16] border border-slate-800 space-y-1">
              <div className="text-slate-500">Current Session Model:</div>
              <div className="text-blue-400 font-medium">{selectedModel}</div>
            </div>

            <div className="p-3 rounded-lg bg-[#090d16] border border-slate-800 space-y-1">
              <div className="text-slate-500">Embedding Model:</div>
              <div className="text-purple-400 font-medium">nomic-embed-text (Ollama)</div>
            </div>
          </div>
        </div>

        {/* Local Storage & Privacy */}
        <div className="rounded-xl border border-slate-800 bg-[#0d1424]/60 p-5 space-y-4">
          <div className="flex items-center gap-2 pb-3 border-b border-slate-800/80">
            <HardDrive className="w-4 h-4 text-purple-400" />
            <h2 className="text-sm font-semibold text-white">On-Premise Storage Paths</h2>
          </div>

          <div className="space-y-3 font-mono text-xs">
            <div className="flex items-center justify-between p-3 rounded-lg bg-[#090d16] border border-slate-800">
              <div>
                <div className="text-slate-300 font-medium">ChromaDB Vector Store:</div>
                <div className="text-slate-500 text-[11px]">data/chromadb (Persistent HNSW Cosine Index)</div>
              </div>
              <Badge variant="purple">{documents.length} docs indexed</Badge>
            </div>

            <div className="flex items-center justify-between p-3 rounded-lg bg-[#090d16] border border-slate-800">
              <div>
                <div className="text-slate-300 font-medium">Document Upload Store:</div>
                <div className="text-slate-500 text-[11px]">data/uploads/ (Local isolated storage)</div>
              </div>
              <Badge variant="slate">Path Protected</Badge>
            </div>

            <div className="flex items-center justify-between p-3 rounded-lg bg-[#090d16] border border-slate-800">
              <div>
                <div className="text-slate-300 font-medium">Application Logs:</div>
                <div className="text-slate-500 text-[11px]">data/logs/app.log (Rotating File Handler)</div>
              </div>
              <Badge variant="emerald">Active</Badge>
            </div>
          </div>
        </div>

        {/* Security & Sovereignty Policy */}
        <div className="p-4 rounded-xl border border-emerald-900/60 bg-emerald-950/20 text-xs text-slate-300 space-y-2">
          <div className="flex items-center gap-2 font-semibold text-emerald-400">
            <ShieldCheck className="w-4 h-4" />
            <span>Sovereignty Audit Declaration</span>
          </div>
          <p className="text-[11.5px] leading-relaxed text-slate-400">
            This workbench instance is operating in <strong>Sovereign Local Mode</strong>. All user queries, embeddings, vector similarity queries, and agent completions execute on this hardware without transmitting packets to external LLM providers or cloud databases.
          </p>
        </div>
      </div>
    </div>
  );
};
