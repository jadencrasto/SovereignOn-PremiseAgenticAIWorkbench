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

      {/* Top Header Bar */}
      <header className="border-b border-slate-200 bg-white px-5 py-3 flex flex-col xl:flex-row xl:items-center justify-between gap-3 shrink-0 select-none shadow-sm z-20 font-sans">
        {/* Top Row: Brand & Quick Role Route Switcher */}
        <div className="flex items-center justify-between gap-4 shrink-0 flex-wrap">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 bg-blue-500 rounded-lg flex items-center justify-center text-white font-bold font-display text-lg shadow-sm">
              S
            </div>
            <div>
              <div className="font-display font-semibold text-lg text-slate-800 tracking-tight leading-none">
                Sovereign Dashboard
              </div>
              <div className="text-[10px] text-slate-500 font-medium tracking-wide mt-1 uppercase">
                Air-Gapped Agentic Workbench
              </div>
            </div>
          </div>

          {/* Quick Role View Direct Links (/admin, /manager, /user) */}
          <div className="flex items-center gap-1.5 bg-slate-50 p-1.5 border border-slate-200 rounded-lg">
            <span className="text-[10px] font-medium text-slate-500 px-1 uppercase">Roles:</span>
            <button
              onClick={() => handleNavigateToRole('/admin')}
              className="px-2.5 py-1 text-[11px] font-medium rounded-md bg-white text-slate-700 hover:bg-slate-100 border border-slate-200 transition-all shadow-sm"
              title="View as Administrator (/admin)"
            >
              👑 Admin
            </button>
            <button
              onClick={() => handleNavigateToRole('/manager')}
              className="px-2.5 py-1 text-[11px] font-medium rounded-md bg-white text-slate-700 hover:bg-slate-100 border border-slate-200 transition-all shadow-sm"
              title="View as Operations Manager (/manager)"
            >
              🛠️ Manager
            </button>
            <button
              onClick={() => handleNavigateToRole('/user')}
              className="px-2.5 py-1 text-[11px] font-medium rounded-md bg-white text-slate-700 hover:bg-slate-100 border border-slate-200 transition-all shadow-sm"
              title="View as Standard User (/user)"
            >
              👤 User
            </button>
          </div>

          {/* Active Model Indicator Chip */}
          <div className="flex items-center gap-2 px-3 py-1.5 bg-blue-50 border border-blue-100 rounded-full text-xs font-medium text-blue-700">
            <span className="text-blue-400">Model:</span>
            <span>{selectedModel || 'QWEN2.5:7B'}</span>
          </div>

          {/* User Profile & Airgap Status */}
          <div className="flex items-center gap-3">
            <div className="px-3 py-1.5 bg-slate-50 border border-slate-200 rounded-full text-[10px] font-medium flex items-center gap-1.5 text-slate-600">
              <span className={`w-2 h-2 rounded-full ${isBackendConnected ? 'bg-emerald-500' : 'bg-rose-500'}`} />
              <span>{isBackendConnected ? 'Connected' : 'Offline'}</span>
            </div>

            <button
              onClick={() => setIsLoginOpen(true)}
              className="px-3 py-1.5 bg-white border border-slate-200 hover:border-blue-300 hover:text-blue-600 rounded-full text-slate-700 font-medium text-xs flex items-center gap-1.5 transition-colors shadow-sm"
            >
              <UserIcon className="w-3.5 h-3.5 text-blue-500" />
              <span>{user ? user.username : 'Operator'}</span>
            </button>
          </div>
        </div>

        {/* Navigation Tabs Strip */}
        <nav className="flex items-center flex-wrap gap-2 bg-slate-50 border border-slate-200 rounded-lg p-1.5">
          {allTabs.map((tab) => {
            const isActive = activeTab === tab.id;
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                title={tab.label}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-all whitespace-nowrap ${
                  isActive
                    ? 'bg-white text-blue-600 shadow-sm border border-slate-200'
                    : 'text-slate-600 hover:text-blue-600 hover:bg-slate-100/50 border border-transparent'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                <span>{tab.label}</span>
                {tab.count !== undefined && tab.count > 0 && (
                  <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${isActive ? 'bg-blue-100 text-blue-700' : 'bg-slate-200 text-slate-600'}`}>
                    {tab.count}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </header>

      {/* Main Full-Screen Native Studio Workspace */}
      <main className="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden bg-[#f0f7ff]">
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
