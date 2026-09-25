/**
 * frontend/src/components/layout/Sidebar.tsx
 * ------------------------------------------
 * Industrial Neo-Brutalist Flight-Deck Navigation
 */

import React, { useState } from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import { useAuth } from '../../context/AuthContext';
import type { ActiveTab } from '../../types';
import {
  Terminal,
  FileSpreadsheet,
  BookOpen,
  Shield,
  Sliders,
  Cpu,
  History,
  Zap,
} from 'lucide-react';
import { LoginModal } from '../auth/LoginModal';

export const Sidebar: React.FC = () => {
  const { activeTab, setActiveTab, documents, isBackendConnected } = useWorkbench();
  const { user, role, isAuthenticated } = useAuth();
  const [isLoginOpen, setIsLoginOpen] = useState(false);

  const navItems = [
    { id: 'demo' as ActiveTab, label: 'Dispatch Benchmarks', icon: Zap, highlight: true },
    { id: 'chat' as ActiveTab, label: 'Operator Terminal', icon: Terminal },
    { id: 'artifacts' as ActiveTab, label: 'Verified Artifacts', icon: FileSpreadsheet },
    { id: 'documents' as ActiveTab, label: 'Knowledge Repo', icon: BookOpen, count: documents.length },
    { id: 'tasks' as ActiveTab, label: 'Task Journal', icon: History },
    { id: 'audit' as ActiveTab, label: 'Compliance Audit', icon: Shield },
    { id: 'health' as ActiveTab, label: 'System Telemetry', icon: Cpu },
    { id: 'settings' as ActiveTab, label: 'Configuration', icon: Sliders },
  ];

  return (
    <>
      <aside className="w-72 bg-white border-r border-slate-200 flex flex-col justify-between select-none shrink-0 h-full font-sans">
        {/* Brand Stamp Header */}
        <div>
          <div className="p-5 border-b border-slate-200 bg-slate-50/50">
            <div className="flex items-center justify-between">
              <div className="font-display font-semibold text-base tracking-tight text-slate-800 flex items-center gap-2">
                <span className="w-3 h-3 bg-blue-500 rounded-full inline-block" />
                Sovereign OS
              </div>
              <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 border border-emerald-200">
                Air-Gap
              </span>
            </div>
            <div className="text-xs text-slate-500 mt-2">
              SIH26117 &bull; Industrial Agent
            </div>
          </div>

          {/* Navigation Matrix */}
          <div className="p-3 space-y-1">
            <div className="px-3 py-2 text-[10px] font-semibold tracking-wider text-slate-400 uppercase">
              Control Matrix
            </div>
            <div className="space-y-1">
              {navItems.map((item) => {
                const isActive = activeTab === item.id;
                return (
                  <button
                    key={item.id}
                    onClick={() => setActiveTab(item.id)}
                    className={`w-full flex items-center justify-between px-3 py-2.5 text-sm transition-all text-left rounded-lg ${
                      isActive
                        ? 'bg-blue-50 text-blue-700 font-medium'
                        : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900 font-normal'
                    }`}
                  >
                    <div className="flex items-center gap-3 truncate">
                      <item.icon className="w-4 h-4" />
                      <span>{item.label}</span>
                    </div>
                    {item.count !== undefined && item.count > 0 && (
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${isActive ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-500'}`}>
                        {item.count}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* Bottom Station Stamp */}
        <div className="p-4 border-t border-slate-200 bg-slate-50 space-y-3">
          {/* Operator ID Button */}
          <button
            onClick={() => setIsLoginOpen(true)}
            className="w-full p-3 bg-white border border-slate-200 hover:border-slate-300 rounded-lg text-left transition-all flex items-center justify-between shadow-sm"
          >
            <div>
              <div className="text-sm font-medium text-slate-800">
                Op: {user ? user.username : 'Local Admin'}
              </div>
              <div className="text-[10px] text-blue-600 uppercase tracking-wider font-semibold mt-0.5">
                {role} Privilege
              </div>
            </div>
            <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${isAuthenticated ? 'bg-emerald-50 text-emerald-600 border-emerald-200' : 'bg-slate-100 text-slate-500 border-slate-200'}`}>
              {isAuthenticated ? 'AUTH OK' : 'LOGIN'}
            </span>
          </button>

          {/* Hard Telemetry Stamp */}
          <div className="p-3 border border-slate-200 bg-white rounded-lg flex items-center justify-between text-xs font-medium shadow-sm">
            <span className="text-slate-500">Network:</span>
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${isBackendConnected ? 'bg-emerald-500' : 'bg-rose-500'}`} />
              <span className={isBackendConnected ? 'text-emerald-600' : 'text-rose-600'}>
                {isBackendConnected ? 'Connected' : 'Offline'}
              </span>
            </div>
          </div>
        </div>
      </aside>

      <LoginModal isOpen={isLoginOpen} onClose={() => setIsLoginOpen(false)} />
    </>
  );
};
