import React from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import { Sidebar } from './Sidebar';
import { StatusBar } from './StatusBar';
import { ToastContainer } from '../common/ToastContainer';
import { ChatView } from '../chat/ChatView';
import { DocumentsView } from '../documents/DocumentsView';
import { ModelsView } from '../models/ModelsView';
import { ToolsView } from '../tools/ToolsView';
import { SettingsView } from '../settings/SettingsView';
import { TaskHistoryView } from '../tasks/TaskHistoryView';
import { AuditDashboard } from '../audit/AuditDashboard';
import { SystemHealth } from '../health/SystemHealth';
import { SecurityDiagnostics } from '../security/SecurityDiagnostics';
import { DemoScenarioLauncher } from '../demo/DemoScenarioLauncher';
import { ArtifactViewer } from '../artifacts/ArtifactViewer';

export const AppLayout: React.FC = () => {
  const { activeTab } = useWorkbench();

  return (
    <div className="flex flex-col h-screen w-screen bg-[#090d16] text-slate-100 overflow-hidden font-sans select-text">
      <ToastContainer />

      <div className="flex flex-1 overflow-hidden">
        {/* Left Sidebar */}
        <Sidebar />

        {/* Main Content View */}
        <main className="flex-1 flex flex-col min-w-0 overflow-hidden bg-[#090d16]">
          {activeTab === 'demo' && <DemoScenarioLauncher />}
          {activeTab === 'chat' && <ChatView />}
          {activeTab === 'tasks' && <TaskHistoryView />}
          {activeTab === 'artifacts' && <ArtifactViewer />}
          {activeTab === 'documents' && <DocumentsView />}
          {activeTab === 'models' && <ModelsView />}
          {activeTab === 'tools' && <ToolsView />}
          {activeTab === 'audit' && <AuditDashboard />}
          {activeTab === 'health' && <SystemHealth />}
          {activeTab === 'security' && <SecurityDiagnostics />}
          {activeTab === 'settings' && <SettingsView />}
        </main>
      </div>


      {/* Bottom Status Bar */}
      <StatusBar />
    </div>
  );
};
