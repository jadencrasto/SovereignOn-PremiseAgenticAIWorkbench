import React from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import { Database, Cpu, Shield, RefreshCw } from 'lucide-react';

export const StatusBar: React.FC = () => {
  const {
    isBackendConnected,
    isCheckingHealth,
    refreshHealth,
    health,
    selectedModel,
    documents,
  } = useWorkbench();

  return (
    <footer className="h-8 bg-[#090e17] border-t border-slate-800/80 px-4 flex items-center justify-between text-[11px] font-mono text-slate-400 select-none shrink-0">
      {/* Left Indicators */}
      <div className="flex items-center gap-4">
        {/* Backend Connectivity */}
        <div className="flex items-center gap-1.5">
          <span
            className={`w-2 h-2 rounded-full ${
              isBackendConnected ? 'bg-emerald-500 shadow-sm shadow-emerald-500/50 animate-pulse' : 'bg-rose-500'
            }`}
          />
          <span className={isBackendConnected ? 'text-emerald-400 font-medium' : 'text-rose-400 font-medium'}>
            {isBackendConnected ? 'LOCAL BACKEND' : 'BACKEND OFFLINE'}
          </span>
          <button
            onClick={refreshHealth}
            title="Refresh connection status"
            className="hover:text-slate-200 transition-colors ml-0.5"
            disabled={isCheckingHealth}
          >
            <RefreshCw className={`w-3 h-3 ${isCheckingHealth ? 'animate-spin text-emerald-400' : ''}`} />
          </button>
        </div>

        <span className="text-slate-700">|</span>

        {/* Model Indicator */}
        <div className="flex items-center gap-1.5">
          <Cpu className="w-3.5 h-3.5 text-blue-400" />
          <span className="text-slate-400">Model:</span>
          <span className="text-slate-200 font-medium">
            {selectedModel || health?.default_model || 'None'}
          </span>
        </div>

        <span className="text-slate-700">|</span>

        {/* RAG Status */}
        <div className="flex items-center gap-1.5">
          <Database className="w-3.5 h-3.5 text-purple-400" />
          <span className="text-slate-400">RAG:</span>
          <span className="text-emerald-400 font-medium">
            {documents.length > 0 ? `Active (${documents.length} docs)` : 'Standby (0 docs)'}
          </span>
        </div>

        <span className="text-slate-700">|</span>

        {/* Vector DB */}
        <div className="flex items-center gap-1.5">
          <span className="text-slate-400">Storage:</span>
          <span className="text-slate-200">ChromaDB Persistent (Local)</span>
        </div>
      </div>

      {/* Right Indicators */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1 text-slate-400">
          <Shield className="w-3 h-3 text-emerald-400" />
          <span>Zero Cloud Telemetry</span>
        </div>
        <span className="text-slate-700">|</span>
        <span className="text-slate-400">v{health?.version || '0.1.0'}</span>
      </div>
    </footer>
  );
};
