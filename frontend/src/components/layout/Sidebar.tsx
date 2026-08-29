import React from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import type { ActiveTab } from '../../types';
import {
  MessageSquare,
  FileText,
  Cpu,
  Wrench,
  Settings,
  ShieldCheck,
  HardDrive,
  ListTodo,
} from 'lucide-react';

export const Sidebar: React.FC = () => {
  const { activeTab, setActiveTab, documents, isBackendConnected } = useWorkbench();

  const navItems: { id: ActiveTab; label: string; icon: React.FC<{ className?: string }>; count?: number }[] = [
    { id: 'chat', label: 'Agent Chat', icon: MessageSquare },
    { id: 'tasks', label: 'Agent Tasks', icon: ListTodo },
    { id: 'documents', label: 'Documents & RAG', icon: FileText, count: documents.length },
    { id: 'models', label: 'Local Models', icon: Cpu },
    { id: 'tools', label: 'Tool Registry', icon: Wrench },
    { id: 'settings', label: 'System Settings', icon: Settings },
  ];

  return (
    <aside className="w-64 bg-[#0c121e] border-r border-slate-800/80 flex flex-col justify-between select-none shrink-0 h-full">
      {/* Brand Header */}
      <div>
        <div className="p-4 border-b border-slate-800/80">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-emerald-950/80 border border-emerald-500/40 flex items-center justify-center text-emerald-400 shadow-md shadow-emerald-950/50">
              <ShieldCheck className="w-5 h-5" />
            </div>
            <div>
              <div className="text-sm font-semibold text-white tracking-tight flex items-center gap-1.5">
                Sovereign AI
                <span className="text-[10px] uppercase font-mono px-1.5 py-0.2 bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 rounded">
                  Local
                </span>
              </div>
              <div className="text-[11px] text-slate-400 font-mono">
                SIH26117 Workbench
              </div>
            </div>
          </div>
        </div>

        {/* Navigation */}
        <nav className="p-3 space-y-1">
          <div className="px-3 py-1.5 text-[10px] font-mono uppercase tracking-wider text-slate-400">
            Navigation
          </div>
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                className={`w-full flex items-center justify-between px-3 py-2 rounded-md text-xs font-medium transition-all ${
                  isActive
                    ? 'bg-slate-800/90 text-emerald-400 border border-emerald-500/20 shadow-sm'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <Icon className={`w-4 h-4 ${isActive ? 'text-emerald-400' : 'text-slate-400'}`} />
                  <span>{item.label}</span>
                </div>
                {item.count !== undefined && item.count > 0 && (
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded-full bg-slate-800 text-slate-300 border border-slate-700">
                    {item.count}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Sovereignty & Privacy Box */}
      <div className="p-3 border-t border-slate-800/80 bg-slate-950/40 space-y-2">
        <div className="p-2.5 rounded-lg border border-slate-800 bg-[#090d16] text-[11px] space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-slate-400 font-medium flex items-center gap-1.5">
              <HardDrive className="w-3.5 h-3.5 text-emerald-400" />
              Sovereignty Mode
            </span>
            <span className="text-[10px] font-mono text-emerald-400 font-semibold uppercase">
              100% On-Prem
            </span>
          </div>
          <p className="text-slate-400 text-[10.5px] leading-tight">
            Documents and inference execute entirely within this host. Zero telemetry.
          </p>
        </div>

        <div className="flex items-center justify-between px-1 text-[11px] text-slate-400 font-mono">
          <span>Ollama Engine</span>
          <span className="flex items-center gap-1">
            <span
              className={`w-2 h-2 rounded-full ${
                isBackendConnected ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'
              }`}
            />
            {isBackendConnected ? 'Active' : 'Offline'}
          </span>
        </div>
      </div>
    </aside>
  );
};
