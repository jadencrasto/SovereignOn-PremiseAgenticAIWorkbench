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
  FileCode,
  Shield,
  Sliders,
  Cpu,
  History,
  User as UserIcon,
  Play,
  Zap,
} from 'lucide-react';
import { LoginModal } from '../auth/LoginModal';

export const Sidebar: React.FC = () => {
  const { activeTab, setActiveTab, documents, isBackendConnected } = useWorkbench();
  const { user, role, isAuthenticated } = useAuth();
  const [isLoginOpen, setIsLoginOpen] = useState(false);

  const navItems = [
    { id: 'demo' as ActiveTab, label: '01 // DISPATCH BENCHMARKS', icon: Zap, highlight: true },
    { id: 'chat' as ActiveTab, label: '02 // OPERATOR TERMINAL', icon: Terminal },
    { id: 'artifacts' as ActiveTab, label: '03 // VERIFIED ARTIFACTS', icon: FileSpreadsheet },
    { id: 'documents' as ActiveTab, label: '04 // KNOWLEDGE REPO', icon: BookOpen, count: documents.length },
    { id: 'tasks' as ActiveTab, label: '05 // TASK JOURNAL', icon: History },
    { id: 'audit' as ActiveTab, label: '06 // COMPLIANCE AUDIT', icon: Shield },
    { id: 'health' as ActiveTab, label: '07 // SYSTEM TELEMETRY', icon: Cpu },
    { id: 'settings' as ActiveTab, label: '08 // CONFIGURATION', icon: Sliders },
  ];

  return (
    <>
      <aside className="w-72 bg-[#0d0e11] border-r-2 border-[#27272a] flex flex-col justify-between select-none shrink-0 h-full font-mono">
        {/* Brand Stamp Header */}
        <div>
          <div className="p-4 border-b-2 border-[#27272a] bg-[#14151a]">
            <div className="flex items-center justify-between">
              <div className="font-display font-black text-sm tracking-tight text-white uppercase flex items-center gap-2">
                <span className="w-3 h-3 bg-[#ffde59] border border-black inline-block" />
                SOVEREIGN // OS
              </div>
              <span className="text-[10px] font-bold px-1.5 py-0.5 bg-[#00ff88] text-black border border-black">
                AIR-GAP
              </span>
            </div>
            <div className="text-[10px] text-zinc-400 mt-1 uppercase tracking-wider">
              SIH26117 &bull; INDUSTRIAL AGENT
            </div>
          </div>

          {/* Navigation Matrix */}
          <div className="p-3 space-y-1.5">
            <div className="px-2 py-1 text-[9px] font-bold tracking-widest text-zinc-500 uppercase">
              // CONTROL MATRIX
            </div>
            <div className="space-y-1">
              {navItems.map((item) => {
                const isActive = activeTab === item.id;
                return (
                  <button
                    key={item.id}
                    onClick={() => setActiveTab(item.id)}
                    className={`w-full flex items-center justify-between px-3 py-2.5 text-xs font-bold transition-all text-left border-2 ${
                      isActive
                        ? 'bg-[#ffde59] text-black border-black brutal-shadow-black'
                        : 'bg-[#14151a] text-zinc-300 border-[#27272a] hover:border-zinc-500 hover:text-white'
                    }`}
                  >
                    <div className="flex items-center gap-2 truncate">
                      <span>{item.label}</span>
                    </div>
                    {item.count !== undefined && item.count > 0 && (
                      <span className={`text-[10px] px-1.5 py-0.2 border ${isActive ? 'bg-black text-white border-black' : 'bg-black text-[#00ff88] border-[#27272a]'}`}>
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
        <div className="p-3 border-t-2 border-[#27272a] bg-[#14151a] space-y-2">
          {/* Operator ID Button */}
          <button
            onClick={() => setIsLoginOpen(true)}
            className="w-full p-2 bg-[#0d0e11] border-2 border-[#27272a] hover:border-zinc-400 text-left transition flex items-center justify-between"
          >
            <div>
              <div className="text-[11px] font-bold text-white uppercase">
                OP: {user ? user.username : 'ADMIN_LOCAL'}
              </div>
              <div className="text-[9px] text-[#ffde59] uppercase font-bold">
                [{role.toUpperCase()}_PRIVILEGE]
              </div>
            </div>
            <span className="text-[9px] font-bold px-1.5 py-0.5 bg-[#27272a] text-zinc-300 border border-zinc-600">
              {isAuthenticated ? 'AUTH_OK' : 'LOGIN'}
            </span>
          </button>

          {/* Hard Telemetry Stamp */}
          <div className="p-2 border-2 border-[#27272a] bg-[#0d0e11] flex items-center justify-between text-[10px] font-bold">
            <span className="text-zinc-400">NET_STATUS:</span>
            <div className="flex items-center gap-1.5">
              <span className={`w-2 h-2 ${isBackendConnected ? 'bg-[#00ff88]' : 'bg-[#ff3b30]'} border border-black`} />
              <span className={isBackendConnected ? 'text-[#00ff88]' : 'text-[#ff3b30]'}>
                {isBackendConnected ? 'LOCAL_BOUND' : 'OFFLINE'}
              </span>
            </div>
          </div>
        </div>
      </aside>

      <LoginModal isOpen={isLoginOpen} onClose={() => setIsLoginOpen(false)} />
    </>
  );
};
