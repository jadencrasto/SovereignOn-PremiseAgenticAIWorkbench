/**
 * frontend/src/components/auth/RolePortalView.tsx
 * ------------------------------------------------
 * Comprehensive Multi-Role Portal & Live RBAC Perspective Showcase.
 * 
 * Supports dedicated routes and simulated views for:
 * 1. 👑 /admin (or /aadmin) — Administrator Command Center (Full Security, User Management, Classified Keys, Tool Governance)
 * 2. 🛠️ /manager (or /operator) — Operations & Plant Manager Console (Human Approvals, Multi-Step Tasks, Defect Remediation)
 * 3. 👤 /user (or /viewer) — Standard User / Field Operator Portal (Read-Only Document Q&A, Topology, Safe Inquiries)
 */

import React, { useState, useEffect } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useWorkbench } from '../../context/WorkbenchContext';
import {
  Shield,
  Lock,
  Unlock,
  Key,
  Users,
  Sliders,
  CheckCircle2,
  AlertTriangle,
  FileText,
  Activity,
  Terminal,
  FileSpreadsheet,
  ArrowRight,
  Sparkles,
  Server,
  Zap,
  Check,
  X,
  Plus,
  Trash2,
  ExternalLink,
  Layers,
  Database,
  Cpu,
  History,
  AlertOctagon,
} from 'lucide-react';
import type { UserRole } from '../../types';

interface RolePortalViewProps {
  initialRole?: UserRole;
}

export const RolePortalView: React.FC<RolePortalViewProps> = ({ initialRole }) => {
  const { role: authRole } = useAuth();
  const { setActiveTab, addToast, documents, selectedModel } = useWorkbench();

  // Active simulated role: 'admin' | 'operator' | 'viewer'
  const [activeRole, setActiveRole] = useState<UserRole>(() => {
    const path = window.location.pathname.toLowerCase();
    if (path.includes('/admin') || path.includes('/aadmin')) return 'admin';
    if (path.includes('/manager') || path.includes('/operator')) return 'operator';
    if (path.includes('/user') || path.includes('/viewer')) return 'viewer';
    return initialRole || authRole || 'admin';
  });

  // Mock User Management state for Admin demo
  const [userList, setUserList] = useState([
    { id: 'usr_01', username: 'admin_lead', role: 'admin', clearance: 'Level 3: Full Airgap', status: 'ACTIVE', lastLogin: 'Just now' },
    { id: 'usr_02', username: 'plant_manager_04', role: 'operator', clearance: 'Level 2: Operations', status: 'ACTIVE', lastLogin: '14m ago' },
    { id: 'usr_03', username: 'process_engineer_desalter', role: 'operator', clearance: 'Level 2: Operations', status: 'ACTIVE', lastLogin: '1h ago' },
    { id: 'usr_04', username: 'field_tech_shift_a', role: 'viewer', clearance: 'Level 1: Read-Only', status: 'ACTIVE', lastLogin: '3h ago' },
    { id: 'usr_05', username: 'compliance_auditor', role: 'viewer', clearance: 'Level 1: Read-Only', status: 'ACTIVE', lastLogin: 'Yesterday' },
  ]);

  const [newUsername, setNewUsername] = useState('');
  const [newUserRole, setNewUserRole] = useState<UserRole>('operator');

  // Sync URL route when switching role
  const handleSwitchRole = (role: UserRole) => {
    setActiveRole(role);
    const targetPath = role === 'admin' ? '/admin' : role === 'operator' ? '/manager' : '/user';
    window.history.pushState({}, '', targetPath);
    addToast('info', `Switched view perspective to: ${role.toUpperCase()} (${targetPath})`);
  };

  // Sync with browser back/forward buttons
  useEffect(() => {
    const handlePopState = () => {
      const path = window.location.pathname.toLowerCase();
      if (path.includes('/admin') || path.includes('/aadmin')) setActiveRole('admin');
      else if (path.includes('/manager') || path.includes('/operator')) setActiveRole('operator');
      else if (path.includes('/user') || path.includes('/viewer')) setActiveRole('viewer');
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const handleAddUser = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newUsername.trim()) return;
    const newUser = {
      id: `usr_${Math.random().toString(36).substring(2, 6)}`,
      username: newUsername.trim().toLowerCase().replace(/\s+/g, '_'),
      role: newUserRole,
      clearance: newUserRole === 'admin' ? 'Level 3: Full Airgap' : newUserRole === 'operator' ? 'Level 2: Operations' : 'Level 1: Read-Only',
      status: 'ACTIVE',
      lastLogin: 'Never',
    };
    setUserList((prev) => [newUser, ...prev]);
    setNewUsername('');
    addToast('success', `Created new ${newUserRole.toUpperCase()} account: ${newUser.username}`);
  };

  const handleDeleteUser = (id: string, name: string) => {
    setUserList((prev) => prev.filter((u) => u.id !== id));
    addToast('info', `Revoked access for user: ${name}`);
  };

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto bg-[#070b14] text-slate-100 p-5 md:p-8 font-sans">
      <div className="max-w-7xl mx-auto w-full space-y-6">
        {/* 1. Interactive Role Perspective Switcher Bar */}
        <div className="bg-[#0f172a]/95 border border-slate-800 rounded-2xl p-4 md:p-5 shadow-2xl backdrop-blur-xl flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-sky-400 animate-pulse" />
              <h1 className="text-base font-bold tracking-tight text-white flex items-center gap-2">
                <span>Enterprise Multi-Role Perspective Navigator</span>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-sky-500/10 text-sky-400 border border-sky-500/30">
                  ROUTE: /{activeRole === 'admin' ? 'admin' : activeRole === 'operator' ? 'manager' : 'user'}
                </span>
              </h1>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              Select any role below to experience the exact access controls, capabilities, and restrictions enforced for each user tier.
            </p>
          </div>

          {/* Role Switcher Pills */}
          <div className="flex items-center gap-2 bg-slate-900/90 p-1.5 rounded-xl border border-slate-800">
            {[
              {
                id: 'admin' as UserRole,
                route: '/admin',
                label: '👑 Admin Portal',
                sub: 'Full Security & Root Access',
                color: 'bg-rose-600 text-white shadow-md shadow-rose-600/40',
              },
              {
                id: 'operator' as UserRole,
                route: '/manager',
                label: '🛠️ Manager Portal',
                sub: 'Planning & Human Approvals',
                color: 'bg-amber-600 text-white shadow-md shadow-amber-600/40',
              },
              {
                id: 'viewer' as UserRole,
                route: '/user',
                label: '👤 User / Viewer Portal',
                sub: 'Read-Only Document Q&A',
                color: 'bg-emerald-600 text-white shadow-md shadow-emerald-600/40',
              },
            ].map((r) => {
              const isSelected = activeRole === r.id;
              return (
                <button
                  key={r.id}
                  onClick={() => handleSwitchRole(r.id)}
                  className={`px-3.5 py-2 rounded-lg text-xs font-semibold flex flex-col items-start transition-all ${
                    isSelected
                      ? r.color
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                  }`}
                >
                  <span className="font-bold">{r.label}</span>
                  <span className="text-[9.5px] opacity-80 font-mono mt-0.5">{r.route}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* 2. SPECIFIC ROLE PORTAL VIEW CONTENT */}

        {/* ========================================================================= */}
        {/* A. ADMIN PORTAL (/admin)                                                  */}
        {/* ========================================================================= */}
        {activeRole === 'admin' && (
          <div className="space-y-6 animate-in fade-in duration-200">
            {/* Header Banner */}
            <div className="bg-gradient-to-r from-rose-950/40 via-slate-900 to-slate-900 border border-rose-800/40 rounded-2xl p-5 shadow-xl">
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div className="flex items-center gap-3.5">
                  <div className="w-12 h-12 rounded-xl bg-rose-600/20 border border-rose-500/30 flex items-center justify-center text-rose-400 shadow-md">
                    <Shield className="w-6 h-6" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-lg font-bold text-white tracking-tight">
                        Sovereign Administrator Command Center
                      </h2>
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-rose-500/20 text-rose-300 border border-rose-500/40 uppercase">
                        LEVEL 3 CLEARANCE &bull; ROOT
                      </span>
                    </div>
                    <p className="text-xs text-slate-300 mt-1">
                      Full access to user administration, Argon2id credentials, cryptographic audit roots, model routing, and restricted plant formulations.
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setActiveTab('audit')}
                    className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold rounded-lg border border-slate-700 flex items-center gap-1.5 transition-all"
                  >
                    <Activity className="w-3.5 h-3.5 text-sky-400" />
                    <span>View Audit Logs</span>
                  </button>
                  <button
                    onClick={() => setActiveTab('security')}
                    className="px-3 py-1.5 bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold rounded-lg shadow-md shadow-rose-600/30 flex items-center gap-1.5 transition-all"
                  >
                    <Lock className="w-3.5 h-3.5" />
                    <span>Security Posture</span>
                  </button>
                </div>
              </div>
            </div>

            {/* Quick Metrics Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="p-4 rounded-xl bg-slate-900/80 border border-slate-800 shadow-lg">
                <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1 flex items-center justify-between">
                  <span>RBAC Users</span>
                  <Users className="w-4 h-4 text-sky-400" />
                </div>
                <div className="text-2xl font-bold text-white font-mono">{userList.length}</div>
                <div className="text-[11px] text-emerald-400 font-medium mt-1 flex items-center gap-1">
                  <CheckCircle2 className="w-3 h-3" /> All sessions Argon2id verified
                </div>
              </div>

              <div className="p-4 rounded-xl bg-slate-900/80 border border-slate-800 shadow-lg">
                <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1 flex items-center justify-between">
                  <span>Active Local Model</span>
                  <Cpu className="w-4 h-4 text-purple-400" />
                </div>
                <div className="text-xl font-bold text-white font-mono uppercase truncate">
                  {selectedModel || 'qwen2.5:7b'}
                </div>
                <div className="text-[11px] text-slate-400 mt-1">100% local GPU inference</div>
              </div>

              <div className="p-4 rounded-xl bg-slate-900/80 border border-slate-800 shadow-lg">
                <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1 flex items-center justify-between">
                  <span>Tool Governance</span>
                  <Sliders className="w-4 h-4 text-amber-400" />
                </div>
                <div className="text-2xl font-bold text-white font-mono">8 Active</div>
                <div className="text-[11px] text-amber-300 mt-1">AST Python sandbox enforced</div>
              </div>

              <div className="p-4 rounded-xl bg-slate-900/80 border border-slate-800 shadow-lg">
                <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1 flex items-center justify-between">
                  <span>Classified Vault</span>
                  <Key className="w-4 h-4 text-rose-400" />
                </div>
                <div className="text-2xl font-bold text-rose-400 font-mono">UNLOCKED</div>
                <div className="text-[11px] text-rose-300 mt-1">SIL-3 SCADA &amp; NiMo Formulas</div>
              </div>
            </div>

            {/* Admin User Management Section */}
            <div className="bg-slate-900/90 border border-slate-800 rounded-2xl p-5 shadow-xl space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-4">
                <div>
                  <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <Users className="w-4 h-4 text-sky-400" />
                    <span>User Account &amp; Access Control Administration</span>
                  </h3>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Manage operator and viewer accounts with dual-boundary cryptographic permission policies.
                  </p>
                </div>

                {/* Add User Form */}
                <form onSubmit={handleAddUser} className="flex items-center gap-2">
                  <input
                    type="text"
                    value={newUsername}
                    onChange={(e) => setNewUsername(e.target.value)}
                    placeholder="New username..."
                    className="px-3 py-1.5 bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-sky-500"
                  />
                  <select
                    value={newUserRole}
                    onChange={(e) => setNewUserRole(e.target.value as UserRole)}
                    className="px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200 focus:outline-none focus:border-sky-500"
                  >
                    <option value="operator">Operator (Manager)</option>
                    <option value="viewer">Viewer (User)</option>
                    <option value="admin">Administrator</option>
                  </select>
                  <button
                    type="submit"
                    className="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white rounded-lg text-xs font-semibold flex items-center gap-1 shadow-sm transition-all"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>Add</span>
                  </button>
                </form>
              </div>

              {/* User Table */}
              <div className="rounded-xl border border-slate-800 overflow-hidden">
                <table className="w-full text-left text-xs border-collapse font-sans">
                  <thead className="bg-slate-800/80 text-slate-300 font-semibold border-b border-slate-700">
                    <tr>
                      <th className="py-2.5 px-4">Account Username</th>
                      <th className="py-2.5 px-4">Assigned Role</th>
                      <th className="py-2.5 px-4">Clearance Tier</th>
                      <th className="py-2.5 px-4">Last Activity</th>
                      <th className="py-2.5 px-4 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800 text-slate-200">
                    {userList.map((u) => (
                      <tr key={u.id} className="hover:bg-slate-800/40 transition-colors">
                        <td className="py-2.5 px-4 font-mono font-semibold text-white">
                          {u.username}
                        </td>
                        <td className="py-2.5 px-4">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                              u.role === 'admin'
                                ? 'bg-rose-950 text-rose-300 border border-rose-800'
                                : u.role === 'operator'
                                ? 'bg-amber-950 text-amber-300 border border-amber-800'
                                : 'bg-emerald-950 text-emerald-300 border border-emerald-800'
                            }`}
                          >
                            {u.role}
                          </span>
                        </td>
                        <td className="py-2.5 px-4 text-slate-400 font-mono text-[11px]">
                          {u.clearance}
                        </td>
                        <td className="py-2.5 px-4 text-slate-400">{u.lastLogin}</td>
                        <td className="py-2.5 px-4 text-right">
                          {u.role !== 'admin' && (
                            <button
                              onClick={() => handleDeleteUser(u.id, u.username)}
                              className="text-slate-400 hover:text-rose-400 p-1 rounded hover:bg-slate-800 transition-colors"
                              title="Delete User"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Classified Secrets Access Deck (Admin Only) */}
            <div className="bg-rose-950/20 border border-rose-900/40 rounded-2xl p-5 shadow-xl space-y-3">
              <div className="flex items-center gap-2 text-rose-400 font-bold text-sm">
                <Key className="w-4 h-4" />
                <span>Restricted Sovereign Secrets &amp; SCADA SIL-3 Key Store</span>
              </div>
              <p className="text-xs text-slate-300">
                These sensitive assets are cryptographically protected and strictly invisible to Manager and Standard User roles:
              </p>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 pt-2">
                <div className="p-3 bg-slate-900/90 border border-rose-800/30 rounded-xl">
                  <span className="text-[10px] font-mono text-rose-400 font-bold block mb-1">
                    SEC_CATALYST_FORMULA
                  </span>
                  <h4 className="text-xs font-semibold text-white">NiMo/CoMo Zeolite Ratio</h4>
                  <p className="text-[11px] text-slate-400 mt-1">Hash: e9b28a71c828d841e4 (+$3.40/bbl impact)</p>
                </div>

                <div className="p-3 bg-slate-900/90 border border-rose-800/30 rounded-xl">
                  <span className="text-[10px] font-mono text-rose-400 font-bold block mb-1">
                    SEC_SCADA_OVERRIDE
                  </span>
                  <h4 className="text-xs font-semibold text-white">SIL-3 Trip Bypass HSM Key</h4>
                  <p className="text-[11px] text-slate-400 mt-1">Slot: HSM_SLOT_0 &bull; Two-Person Rule</p>
                </div>

                <div className="p-3 bg-slate-900/90 border border-rose-800/30 rounded-xl">
                  <span className="text-[10px] font-mono text-rose-400 font-bold block mb-1">
                    SEC_LEDGER_ROOT
                  </span>
                  <h4 className="text-xs font-semibold text-white">Master Ed25519 Root Authority</h4>
                  <p className="text-[11px] text-slate-400 mt-1">Signs all industrial compliance artifacts</p>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ========================================================================= */}
        {/* B. MANAGER / OPERATOR PORTAL (/manager)                                   */}
        {/* ========================================================================= */}
        {activeRole === 'operator' && (
          <div className="space-y-6 animate-in fade-in duration-200">
            {/* Header Banner */}
            <div className="bg-gradient-to-r from-amber-950/40 via-slate-900 to-slate-900 border border-amber-800/40 rounded-2xl p-5 shadow-xl">
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div className="flex items-center gap-3.5">
                  <div className="w-12 h-12 rounded-xl bg-amber-600/20 border border-amber-500/30 flex items-center justify-center text-amber-400 shadow-md">
                    <Sliders className="w-6 h-6" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-lg font-bold text-white tracking-tight">
                        Plant Operations &amp; Engineering Console
                      </h2>
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 uppercase">
                        LEVEL 2 CLEARANCE &bull; OPERATOR
                      </span>
                    </div>
                    <p className="text-xs text-slate-300 mt-1">
                      Equipped for autonomous multi-step planning, Human-in-the-loop task approvals, NDT defect remediation, and SOP document ingestion.
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setActiveTab('tasks')}
                    className="px-3.5 py-1.5 bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold rounded-lg shadow-md shadow-amber-600/30 flex items-center gap-1.5 transition-all"
                  >
                    <History className="w-3.5 h-3.5" />
                    <span>Review Tasks &amp; Approvals</span>
                  </button>
                  <button
                    onClick={() => setActiveTab('chat')}
                    className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold rounded-lg border border-slate-700 flex items-center gap-1.5 transition-all"
                  >
                    <Terminal className="w-3.5 h-3.5 text-sky-400" />
                    <span>Open Agent Chat</span>
                  </button>
                </div>
              </div>
            </div>

            {/* Operator Capabilities Grid */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {/* Human-in-the-loop Gate */}
              <div className="p-5 rounded-xl bg-slate-900/90 border border-slate-800 shadow-lg space-y-3">
                <div className="flex items-center justify-between text-amber-400">
                  <span className="text-xs font-bold uppercase tracking-wider flex items-center gap-1.5">
                    <CheckCircle2 className="w-4 h-4" />
                    <span>Human Approval Gate</span>
                  </span>
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-amber-500/10 border border-amber-500/30">
                    ENABLED
                  </span>
                </div>
                <p className="text-xs text-slate-300 leading-relaxed">
                  As Manager/Operator, you hold cryptographic signing authority to review and approve high-risk operations (e.g. <code>file_write</code>, <code>docx_create</code>, <code>code_execution</code>) with SHA-256 parameter validation.
                </p>
                <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between text-[11px] text-slate-400">
                  <span>Mutating Tools:</span>
                  <span className="text-emerald-400 font-semibold font-mono">AUTHORIZED</span>
                </div>
              </div>

              {/* NDT Failure Modes & Remediation */}
              <div className="p-5 rounded-xl bg-slate-900/90 border border-slate-800 shadow-lg space-y-3">
                <div className="flex items-center justify-between text-sky-400">
                  <span className="text-xs font-bold uppercase tracking-wider flex items-center gap-1.5">
                    <AlertTriangle className="w-4 h-4 text-amber-400" />
                    <span>Defect Tolerances</span>
                  </span>
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-sky-500/10 border border-sky-500/30">
                    UNLOCKED
                  </span>
                </div>
                <p className="text-xs text-slate-300 leading-relaxed">
                  Inspect active failure modes (Pitting corrosion, valve stem galling, graphite oxidation, pump cavitation) cross-referenced against API 570 and ASME standards.
                </p>
                <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between text-[11px] text-slate-400">
                  <span>Engineering Limits:</span>
                  <span className="text-sky-300 font-semibold font-mono">VISIBLE</span>
                </div>
              </div>

              {/* Ingestion & RAG Indexing */}
              <div className="p-5 rounded-xl bg-slate-900/90 border border-slate-800 shadow-lg space-y-3">
                <div className="flex items-center justify-between text-emerald-400">
                  <span className="text-xs font-bold uppercase tracking-wider flex items-center gap-1.5">
                    <FileText className="w-4 h-4" />
                    <span>Document Ingestion</span>
                  </span>
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30">
                    {documents.length} DOCS
                  </span>
                </div>
                <p className="text-xs text-slate-300 leading-relaxed">
                  Upload PDF, DOCX, TXT, and Markdown engineering runbooks into local ChromaDB with automatic sliding-window chunking.
                </p>
                <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between text-[11px] text-slate-400">
                  <span>Write Permissions:</span>
                  <span className="text-emerald-400 font-semibold font-mono">ACTIVE</span>
                </div>
              </div>
            </div>

            {/* RBAC Boundary Notification Banner */}
            <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 text-xs text-slate-400 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Lock className="w-4 h-4 text-rose-400" />
                <span>
                  <strong>RBAC Security Policy:</strong> Level 3 Classified SCADA master override keys and User Account Deletion are restricted to Administrator accounts.
                </span>
              </div>
              <button
                onClick={() => handleSwitchRole('admin')}
                className="text-sky-400 hover:text-sky-300 font-semibold flex items-center gap-1"
              >
                <span>Elevate to Admin</span>
                <ArrowRight className="w-3 h-3" />
              </button>
            </div>
          </div>
        )}

        {/* ========================================================================= */}
        {/* C. USER / VIEWER PORTAL (/user)                                           */}
        {/* ========================================================================= */}
        {activeRole === 'viewer' && (
          <div className="space-y-6 animate-in fade-in duration-200">
            {/* Header Banner */}
            <div className="bg-gradient-to-r from-emerald-950/40 via-slate-900 to-slate-900 border border-emerald-800/40 rounded-2xl p-5 shadow-xl">
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div className="flex items-center gap-3.5">
                  <div className="w-12 h-12 rounded-xl bg-emerald-600/20 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shadow-md">
                    <CheckCircle2 className="w-6 h-6" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-lg font-bold text-white tracking-tight">
                        Field Analyst &amp; General User Portal
                      </h2>
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 uppercase">
                        LEVEL 1 CLEARANCE &bull; VIEWER
                      </span>
                    </div>
                    <p className="text-xs text-slate-300 mt-1">
                      Read-only sovereign Q&amp;A over company technical documents, public telemetry monitoring, and plant topology exploration.
                    </p>
                  </div>
                </div>

                <button
                  onClick={() => setActiveTab('chat')}
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-lg shadow-md shadow-emerald-600/30 flex items-center gap-1.5 transition-all"
                >
                  <Terminal className="w-3.5 h-3.5" />
                  <span>Start Document Q&amp;A</span>
                </button>
              </div>
            </div>

            {/* Viewer Capabilities & Guardrails */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Allowed Capabilities */}
              <div className="p-5 rounded-xl bg-slate-900/90 border border-slate-800 shadow-lg space-y-3">
                <h3 className="text-xs font-bold text-emerald-400 uppercase tracking-wider flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4" />
                  <span>Authorized Viewer Features</span>
                </h3>
                <ul className="space-y-2 text-xs text-slate-300">
                  <li className="flex items-start gap-2">
                    <Check className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                    <span><strong>Grounded RAG Search:</strong> Ask natural language technical questions over ingested manuals with exact chunk citations.</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <Check className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                    <span><strong>Plant Topology Explorer:</strong> View public equipment assets (Pumps, Valves, Columns) and live sensor probes.</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <Check className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                    <span><strong>Safe Arithmetic Tools:</strong> Deterministic calculations without backend state mutation.</span>
                  </li>
                </ul>
              </div>

              {/* Enforced RBAC Guardrails */}
              <div className="p-5 rounded-xl bg-slate-900/90 border border-slate-800 shadow-lg space-y-3">
                <h3 className="text-xs font-bold text-amber-400 uppercase tracking-wider flex items-center gap-1.5">
                  <Shield className="w-4 h-4" />
                  <span>Enforced Security Restrictions (Viewer)</span>
                </h3>
                <ul className="space-y-2 text-xs text-slate-400">
                  <li className="flex items-start gap-2">
                    <X className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                    <span><strong>Mutating Tools Blocked:</strong> <code>file_write</code>, <code>docx_create</code>, and Python <code>code_execution</code> require Operator approval.</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <X className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                    <span><strong>Approval Gate Disabled:</strong> Viewers cannot sign off on high-risk task plans.</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <X className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                    <span><strong>Classified Data Redacted:</strong> Proprietary catalysts and root encryption keys are masked.</span>
                  </li>
                </ul>
              </div>
            </div>

            {/* Quick Elevation Trigger for Demonstration */}
            <div className="p-4 rounded-xl bg-slate-900/80 border border-slate-800 flex items-center justify-between text-xs">
              <span className="text-slate-400">
                Want to test executing multi-step mutating plans or reviewing approvals?
              </span>
              <button
                onClick={() => handleSwitchRole('operator')}
                className="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white rounded-lg font-semibold flex items-center gap-1 transition-all"
              >
                <span>Switch to Manager Perspective</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
