/**
 * frontend/src/components/layout/AppLayout.tsx
 * --------------------------------------------
 * Single-Window Engineering Console (White & Light Blue Industrial Theme)
 * Displays all 13 Core Workbench Modules with Direct Multi-Role URL Routing
 * (/admin, /manager, /user).
 */

import React, { useState, useEffect } from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import { useAuth } from '../../context/AuthContext';
import { ToastContainer } from '../common/ToastContainer';
import { ChatView } from '../chat/ChatView';
import { DocumentsView } from '../documents/DocumentsView';
import { ModelScanner } from '../models/ModelScanner';
import { CompanyKnowledgeGraph } from '../graph/CompanyKnowledgeGraph';
import { ToolsView } from '../tools/ToolsView';
import { SettingsView } from '../settings/SettingsView';
import { TaskHistoryView } from '../tasks/TaskHistoryView';
import { AuditDashboard } from '../audit/AuditDashboard';
import { SystemHealth } from '../health/SystemHealth';
import { SecurityDiagnostics } from '../security/SecurityDiagnostics';
import { DemoScenarioLauncher } from '../demo/DemoScenarioLauncher';
import { ArtifactViewer } from '../artifacts/ArtifactViewer';
import { RolePortalView } from '../auth/RolePortalView';
import { StatusBar } from './StatusBar';
import type { ActiveTab } from '../../types';
import {
  Zap,
  Terminal,
  FileSpreadsheet,
  BookOpen,
  History,
  Shield,
  Cpu,
  Sliders,
  User as UserIcon,
  Search,
  Network,
  Wrench,
  Lock,
  Users,
} from 'lucide-react';
import { LoginModal } from '../auth/LoginModal';

interface TabItem {
  id: ActiveTab;
  label: string;
  shortLabel: string;
  icon: React.FC<{ className?: string }>;
  count?: number;
  badge?: string;
  group: 'operations' | 'knowledge' | 'system';
}

export const AppLayout: React.FC = () => {
  const { activeTab, setActiveTab, isBackendConnected, documents, selectedModel } = useWorkbench();
  const { user } = useAuth();
  const [isLoginOpen, setIsLoginOpen] = useState(false);

  // Check URL pathname on mount and on history changes (/admin, /manager, /user)
  useEffect(() => {
    const checkRoute = () => {
      const path = window.location.pathname.toLowerCase();
      if (
        path.includes('/admin') ||
        path.includes('/aadmin') ||
        path.includes('/manager') ||
        path.includes('/operator') ||
        path.includes('/user') ||
        path.includes('/viewer')
      ) {
        setActiveTab('roles');
      }
    };

    checkRoute();
    window.addEventListener('popstate', checkRoute);
    return () => window.removeEventListener('popstate', checkRoute);
  }, [setActiveTab]);

  const allTabs: TabItem[] = [
    // 1. Operations & Execution
    { id: 'demo', label: 'Demo Scenarios', shortLabel: 'PROCEDURES', icon: Zap, group: 'operations' },
    { id: 'chat', label: 'Agent Chat', shortLabel: 'TERMINAL', icon: Terminal, group: 'operations' },
    { id: 'tasks', label: 'Agent Tasks', shortLabel: 'TASKS', icon: History, group: 'operations' },
    { id: 'artifacts', label: 'Artifacts & Reports', shortLabel: 'REPORTS', icon: FileSpreadsheet, group: 'operations' },

    // 2. Knowledge & Models
    { id: 'documents', label: 'Documents & RAG', shortLabel: 'DOCS', icon: BookOpen, count: documents.length, group: 'knowledge' },
    { id: 'graph', label: 'Knowledge Graph', shortLabel: 'GRAPH', icon: Network, group: 'knowledge' },
    { id: 'models', label: 'Local Models', shortLabel: 'MODELS', icon: Search, group: 'knowledge' },
    { id: 'tools', label: 'Tool Registry', shortLabel: 'TOOLS', icon: Wrench, group: 'knowledge' },

    // 3. Security, Health & System
    { id: 'roles', label: 'RBAC Portals', shortLabel: 'ROLES (/admin)', icon: Users, group: 'system' },
    { id: 'audit', label: 'Audit Log', shortLabel: 'AUDIT', icon: Shield, group: 'system' },
    { id: 'health', label: 'System Health', shortLabel: 'HEALTH', icon: Cpu, group: 'system' },
    { id: 'security', label: 'Security Posture', shortLabel: 'SECURITY', icon: Lock, group: 'system' },
    { id: 'settings', label: 'Settings', shortLabel: 'CONFIG', icon: Sliders, group: 'system' },
  ];

  const handleNavigateToRole = (path: string) => {
    window.history.pushState({}, '', path);
    setActiveTab('roles');
  };

  return (
    <div className="flex flex-col h-screen w-screen bg-[#f0f7ff] text-[#0f172a] overflow-hidden font-sans select-text">
      <ToastContainer />

      {/* Top Header Bar (White & Light Blue Industrial Style) */}
      <header className="border-b-2 border-[#cbd5e1] bg-white px-4 py-2 flex flex-col xl:flex-row xl:items-center justify-between gap-2 shrink-0 select-none brutal-shadow-sky z-20 font-mono">
        {/* Top Row: Brand & Quick Role Route Switcher */}
        <div className="flex items-center justify-between gap-3 shrink-0 flex-wrap">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-[#0284c7] border-2 border-black flex items-center justify-center text-white font-black font-display text-sm shadow-sm">
              S
            </div>
            <div>
              <div className="font-display font-black text-sm text-[#0f172a] tracking-tight leading-none uppercase">
                SOVEREIGN // STATION
              </div>
              <div className="text-[9px] text-slate-500 font-mono tracking-wider mt-0.5 font-bold">
                AIR-GAPPED AGENTIC WORKBENCH
              </div>
            </div>
          </div>

          {/* Quick Role View Direct Links (/admin, /manager, /user) */}
          <div className="flex items-center gap-1 bg-[#f1f5f9] p-1 border border-[#cbd5e1] rounded">
            <span className="text-[9px] font-bold text-slate-500 px-1 uppercase">ROLES:</span>
            <button
              onClick={() => handleNavigateToRole('/admin')}
              className="px-2 py-0.5 text-[10px] font-bold uppercase rounded bg-rose-50 text-rose-700 hover:bg-rose-600 hover:text-white border border-rose-200 transition-all"
              title="View as Administrator (/admin)"
            >
              👑 /admin
            </button>
            <button
              onClick={() => handleNavigateToRole('/manager')}
              className="px-2 py-0.5 text-[10px] font-bold uppercase rounded bg-amber-50 text-amber-800 hover:bg-amber-600 hover:text-white border border-amber-200 transition-all"
              title="View as Operations Manager (/manager)"
            >
              🛠️ /manager
            </button>
            <button
              onClick={() => handleNavigateToRole('/user')}
              className="px-2 py-0.5 text-[10px] font-bold uppercase rounded bg-emerald-50 text-emerald-800 hover:bg-emerald-600 hover:text-white border border-emerald-200 transition-all"
              title="View as Standard User (/user)"
            >
              👤 /user
            </button>
          </div>

          {/* Active Model Indicator Chip */}
          <div className="flex items-center gap-1.5 px-2.5 py-1 bg-[#f0f9ff] border border-[#bae6fd] text-[10px] font-bold text-[#0369a1]">
            <span className="text-slate-400">MODEL:</span>
            <span className="text-[#0284c7] uppercase">{selectedModel || 'QWEN2.5:7B'}</span>
          </div>

          {/* User Profile & Airgap Status */}
          <div className="flex items-center gap-2">
            <div className="px-2 py-0.5 bg-[#f0f9ff] border border-[#bae6fd] text-[9px] font-bold flex items-center gap-1 text-[#0369a1]">
              <span className={`w-1.5 h-1.5 ${isBackendConnected ? 'bg-[#059669]' : 'bg-[#e11d48]'}`} />
              <span>{isBackendConnected ? 'AIRGAP_OK' : 'OFFLINE'}</span>
            </div>

            <button
              onClick={() => setIsLoginOpen(true)}
              className="px-2.5 py-1 bg-white border border-[#cbd5e1] hover:border-[#0284c7] text-slate-700 font-bold text-[11px] uppercase flex items-center gap-1"
            >
              <UserIcon className="w-3 h-3 text-[#0284c7]" />
              <span>{user ? user.username : 'OPERATOR'}</span>
            </button>
          </div>
        </div>

        {/* Navigation Tabs Strip (All 13 Modules Visible) */}
        <nav className="flex items-center flex-wrap gap-1 bg-[#f8fafc] border border-[#cbd5e1] p-1">
          {allTabs.map((tab) => {
            const isActive = activeTab === tab.id;
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                title={tab.label}
                className={`flex items-center gap-1 px-2.5 py-1.5 text-[11px] font-bold uppercase transition-all whitespace-nowrap ${
                  isActive
                    ? 'bg-[#0284c7] text-white border border-black brutal-shadow-dark font-black'
                    : 'text-slate-600 hover:text-[#0284c7] hover:bg-[#e0f2fe] border border-transparent'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                <span>{tab.shortLabel}</span>
                {tab.count !== undefined && tab.count > 0 && (
                  <span className={`text-[9px] px-1 py-0.2 ${isActive ? 'bg-black text-white' : 'bg-[#e2e8f0] text-[#0369a1]'}`}>
                    {tab.count}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </header>

      {/* Main Full-Screen Native Studio Workspace */}
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden bg-[#f0f7ff]">
        {activeTab === 'demo' && <DemoScenarioLauncher />}
        {activeTab === 'chat' && <ChatView />}
        {activeTab === 'tasks' && <TaskHistoryView />}
        {activeTab === 'artifacts' && <ArtifactViewer />}
        {activeTab === 'documents' && <DocumentsView />}
        {activeTab === 'graph' && <CompanyKnowledgeGraph />}
        {activeTab === 'models' && <ModelScanner />}
        {activeTab === 'tools' && <ToolsView />}
        {activeTab === 'roles' && <RolePortalView />}
        {activeTab === 'audit' && <AuditDashboard />}
        {activeTab === 'health' && <SystemHealth />}
        {activeTab === 'security' && <SecurityDiagnostics />}
        {activeTab === 'settings' && <SettingsView />}
      </main>

      {/* Persistent Bottom Status Telemetry Bar */}
      <StatusBar />

      <LoginModal isOpen={isLoginOpen} onClose={() => setIsLoginOpen(false)} />
    </div>
  );
};
