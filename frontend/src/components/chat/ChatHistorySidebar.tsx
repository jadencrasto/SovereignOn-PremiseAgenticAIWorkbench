/**
 * frontend/src/components/chat/ChatHistorySidebar.tsx
 * ---------------------------------------------------
 * Server-authorized conversation history drawer. Legacy browser records are
 * deliberately not read because their ownership cannot be established.
 */

import React, { useState, useEffect, useMemo, useRef } from 'react';
import { clearSession, fetchSessions, fetchSession, legacyExportAllowed, renameSession } from '../../api/chat';
import { ApiError } from '../../api/client';
import { loadOwnedHistory, deleteOwnedHistory } from './historyRequests';
import type { ChatMessage } from '../../types';
import {
  MessageSquare,
  Plus,
  Trash2,
  Edit2,
  Check,
  X,
  Search,
  Clock,
  Download,
  ChevronLeft,
  Calendar,
} from 'lucide-react';

export interface ChatSession {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
  model: string;
}

export async function loadSession(id: string): Promise<ChatSession> {
  const record = await fetchSession(id);
  const messages: ChatMessage[] = record.messages.filter(m => m.role !== 'system').map((m, i) => ({
    id: m.id || `${id}-${i}`, role: m.role, content: m.content,
    timestamp: m.created_at ? new Date(m.created_at).toLocaleString() : '',
    toolEvents: m.tool_events || [], withheld: m.withheld === true,
  }));
  for (const task of record.tasks || []) {
    const taskStatus = task.status.replace(/[^a-zA-Z0-9_-]/g, '');
    const approvalStates = task.approval_statuses.join(', ') || 'none';
    messages.push({ id: `task-state-${task.task_id}`, role: 'assistant', timestamp: '',
      content: `**Task status:** ${taskStatus}. **Approval history:** ${approvalStates}. This is read-only; open Tasks for current state.` });
  }
  return { id, messages, title: record.title || messages.find(m => m.role === 'user')?.content.slice(0, 38) || 'Conversation',
    createdAt: record.created_at ? Date.parse(record.created_at) || 0 : 0,
    updatedAt: record.updated_at ? Date.parse(record.updated_at) || 0 : 0, model: '' };
}

interface ChatHistorySidebarProps {
  isOpen: boolean;
  onClose: () => void;
  currentSessionId: string;
  onSelectSession: (session: ChatSession) => void;
  onNewChat: () => void;
  onRenamed?: (id: string, title: string) => void;
  onDeleted?: () => void;
  refreshKey?: number;
}

export const ChatHistorySidebar: React.FC<ChatHistorySidebarProps> = ({
  isOpen,
  onClose,
  currentSessionId,
  onSelectSession,
  onNewChat,
  onRenamed,
  onDeleted,
  refreshKey = 0,
}) => {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState<string>('');
  const [error, setError] = useState<string>('');
  const [deleting, setDeleting] = useState(false);
  const [loading, setLoading] = useState(false);
  const [canExportLegacy, setCanExportLegacy] = useState(false);
  const generation = useRef(0);

  useEffect(() => {
    if (!isOpen) return;
    const current = ++generation.current;
    setError('');
    setLoading(true);
    void legacyExportAllowed().then(policy => {
      if (generation.current === current) setCanExportLegacy(policy.allowed);
    }).catch(() => { if (generation.current === current) setCanExportLegacy(false); });
    (async () => {
      try {
        const records = await loadOwnedHistory(fetchSessions, loadSession, () => generation.current === current);
        if (records) setSessions(records);
      } catch {
        if (generation.current === current) setError('Unable to load conversation history. Please sign in or try again.');
      } finally {
        if (generation.current === current) setLoading(false);
      }
    })();
    return () => { generation.current++; };
  }, [isOpen, refreshKey]);

  const filteredSessions = useMemo(() => {
    return sessions.filter((s) => {
      if (!searchQuery.trim()) return true;
      const q = searchQuery.toLowerCase();
      return s.title.toLowerCase().includes(q);
    });
  }, [sessions, searchQuery]);

  const removeSession = async (id: string) => {
    const current = ++generation.current;
    setLoading(false);
    setDeleting(true);
    setError('');
    try {
      if (!await deleteOwnedHistory(id, clearSession, () => generation.current === current)) return;
      setSessions(prev => prev.filter(s => s.id !== id));
      if (id === currentSessionId) onNewChat();
      onDeleted?.();
    } catch (err) {
      if (generation.current === current && err instanceof ApiError && err.status === 404) {
        setSessions(prev => prev.filter(s => s.id !== id));
        if (id === currentSessionId) onNewChat();
        onDeleted?.();
        setError('Conversation is no longer available.');
      } else if (generation.current === current) {
        setError('Conversation was not deleted. Check your session and connection, then try again.');
      }
    } finally {
      setDeleting(false);
    }
  };

  const handleDelete = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (!deleting && window.confirm('Delete this conversation from the server?')) void removeSession(id);
  };

  const handleStartRename = (e: React.MouseEvent, s: ChatSession) => {
    e.stopPropagation();
    setEditingId(s.id);
    setEditTitle(s.title);
  };

  const handleSaveRename = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (editTitle.trim()) {
      const current = ++generation.current;
      setLoading(false);
      try {
        const result = await renameSession(id, editTitle.trim());
        if (generation.current === current) {
          setSessions(prev => prev.map(s => s.id === id ? { ...s, title: result.title } : s));
          onRenamed?.(id, result.title);
        }
      } catch { if (generation.current === current) setError('Conversation could not be renamed.'); }
    }
    setEditingId(null);
  };

  const handleExportMarkdown = async (e: React.MouseEvent, session: ChatSession) => {
    e.stopPropagation();
    const current = ++generation.current;
    setLoading(false);
    let s: ChatSession;
    try {
      s = await loadSession(session.id);
      if (generation.current !== current) return;
    } catch (err) {
      if (generation.current === current) {
        if (err instanceof ApiError && err.status === 404) {
          setSessions(prev => prev.filter(item => item.id !== session.id));
          if (session.id === currentSessionId) onNewChat();
          onDeleted?.();
          setError('Conversation is no longer available.');
        } else setError('Unable to revalidate conversation access for export.');
      }
      return;
    }
    let md = `# Sovereign AI Workbench — Chat Transcript\n`;
    md += `**Session:** ${s.title}\n`;
    md += `**Date:** ${new Date(s.createdAt).toLocaleString()}\n`;
    md += `**Model:** ${s.model}\n\n---\n\n`;

    for (const m of s.messages) {
      md += `### ${m.role === 'user' ? '👤 User' : '🤖 Sovereign Assistant'} (${m.timestamp})\n\n`;
      md += `${m.withheld ? '[Response withheld: source access cannot be verified.]' : m.content}\n\n`;
      if (!m.withheld && m.sources && m.sources.length > 0) {
        md += `**Sources:**\n`;
        for (const src of m.sources) {
          md += `- ${src.filename} (score: ${(src.score * 100).toFixed(1)}%)\n`;
        }
        md += `\n`;
      }
    }

    const blob = new Blob([md], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `chat_${s.title.toLowerCase().replace(/[^a-z0-9]/g, '_')}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const formatRelativeTime = (timestamp: number) => {
    if (!timestamp) return 'Date unavailable';
    const now = Date.now();
    const diff = Math.floor((now - timestamp) / 1000);
    if (diff < 60) return 'Just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
    return new Date(timestamp).toLocaleDateString();
  };

  if (!isOpen) return null;

  return (
    <div className="absolute inset-y-0 left-0 z-30 w-80 max-w-full lg:w-96 bg-white border-r border-slate-200 flex flex-col shadow-2xl backdrop-blur-xl animate-in slide-in-from-left duration-200 font-sans">
      {/* Top Header */}
      <div className="p-4 border-b border-slate-200 flex items-center justify-between gap-2 bg-slate-50">
        <div className="flex items-center gap-2">
          <Clock className="w-4 h-4 text-blue-500" />
          <h2 className="text-sm font-semibold text-slate-800 tracking-tight">Chat History</h2>
          <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-slate-200 text-slate-600 border border-slate-300">
            {sessions.length} sessions
          </span>
        </div>

        <button
          onClick={onClose}
          className="p-1 text-slate-400 hover:text-slate-700 rounded hover:bg-slate-200 transition-colors"
          title="Close History"
        >
          <ChevronLeft className="w-5 h-5" />
        </button>
      </div>

      {/* New Conversation Button */}
      <div className="p-3 border-b border-slate-100">
        <button
          onClick={() => {
            onNewChat();
            onClose();
          }}
          className="w-full py-2.5 px-3.5 bg-blue-500 hover:bg-blue-600 text-white rounded-lg font-medium text-sm flex items-center justify-center gap-2 shadow-sm transition-all"
        >
          <Plus className="w-4 h-4" />
          <span>New Conversation</span>
        </button>
      </div>

      {/* Search Input */}
      <div className="px-3 pt-3 pb-2">
        <div className="relative">
          <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            aria-label="Search conversation titles"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search conversation titles..."
            className="w-full pl-9 pr-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400 font-sans"
          />
        </div>
      </div>

      {error && <p role="alert" className="px-3 text-sm text-red-700">{error}</p>}
      {/* Session List */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {loading ? <p role="status" className="p-4 text-sm text-slate-500">Loading conversations...</p> : filteredSessions.length === 0 ? (
          <div className="py-12 px-4 text-center text-slate-500">
            <MessageSquare className="w-8 h-8 mx-auto mb-3 opacity-40 text-slate-400" />
            <p className="text-sm font-medium text-slate-600">No chat history found</p>
            <p className="text-xs text-slate-500 mt-1">
              Start chatting to save multi-turn conversations automatically.
            </p>
          </div>
        ) : (
          filteredSessions.map((s) => {
            const isCurrent = s.id === currentSessionId;
            const isEditing = editingId === s.id;

            return (
              <div
                key={s.id}
                role="group"
                aria-label={`Conversation: ${s.title}. Press Enter to open.`}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (!isEditing && e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
                    e.preventDefault();
                    e.currentTarget.click();
                  }
                }}
                onClick={async () => {
                  if (!isEditing) {
                    const current = ++generation.current;
                    setLoading(false);
                    try {
                      const fresh = await loadSession(s.id);
                      if (generation.current !== current) return;
                      onSelectSession(fresh);
                      onClose();
                    } catch (err) {
                      if (generation.current !== current) return;
                      if (err instanceof ApiError && err.status === 404) {
                        setSessions(prev => prev.filter(item => item.id !== s.id));
                        if (s.id === currentSessionId) onNewChat();
                        onDeleted?.();
                        setError('Conversation is no longer available.');
                      } else setError('Unable to revalidate conversation access.');
                    }
                  }
                }}
                className={`group p-3 rounded-xl border transition-all cursor-pointer relative ${
                  isCurrent
                    ? 'bg-blue-50 border-blue-200 shadow-sm'
                    : 'bg-white hover:bg-slate-50 border-slate-200'
                }`}
              >
                {/* Title and Edit Field */}
                <div className="flex items-start justify-between gap-2 mb-2">
                  {isEditing ? (
                    <div className="flex items-center gap-1 flex-1">
                      <input
                        type="text"
                        aria-label="Conversation title"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                        className="flex-1 bg-white border border-blue-400 rounded px-2 py-1 text-sm text-slate-800 focus:outline-none focus:ring-1 focus:ring-blue-400"
                        autoFocus
                      />
                      <button
                        onClick={(e) => handleSaveRename(e, s.id)}
                        className="p-1.5 text-emerald-600 hover:bg-emerald-50 rounded"
                        title="Save"
                      >
                        <Check className="w-4 h-4" />
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditingId(null);
                        }}
                        className="p-1.5 text-slate-500 hover:bg-slate-100 rounded"
                        title="Cancel"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  ) : (
                    <h3 className={`text-sm font-semibold tracking-tight truncate flex-1 ${isCurrent ? 'text-blue-700' : 'text-slate-800'}`}>
                      {s.title}
                    </h3>
                  )}

                  {/* Actions (Rename, Export, Delete) */}
                  {!isEditing && (
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        onClick={(e) => handleStartRename(e, s)}
                        className="p-1 text-slate-400 hover:text-blue-600 hover:bg-blue-50 rounded"
                        title="Rename"
                      >
                        <Edit2 className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={(e) => handleExportMarkdown(e, s)}
                        className="p-1 text-slate-400 hover:text-blue-600 hover:bg-blue-50 rounded"
                        title="Export Markdown"
                      >
                        <Download className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={(e) => handleDelete(e, s.id)}
                        disabled={deleting}
                        className="p-1 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded"
                        title="Delete"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}
                </div>

                {/* Subtitle Telemetry */}
                <div className="flex items-center justify-between text-xs text-slate-500">
                  <span className="flex items-center gap-1.5">
                    <Calendar className="w-3.5 h-3.5" />
                    <span>{formatRelativeTime(s.updatedAt)}</span>
                  </span>

                </div>
              </div>
            );
          })
        )}
      </div>

      {canExportLegacy && (
        <button className="p-3 text-sm text-slate-500" onClick={async () => {
          if (!confirm('Export unverified old browser history? It may contain another person’s private data. Save it only to a trusted location.')) return;
          const current = generation.current;
          try {
            const policy = await legacyExportAllowed();
            if (!policy.allowed || generation.current !== current) return;
            const raw = localStorage.getItem('sovereign_chat_sessions_v2');
            if (raw === null) { setError('No legacy browser history was found.'); return; }
            const url = URL.createObjectURL(new Blob([raw], { type: 'application/json' }));
            const link = document.createElement('a');
            link.href = url;
            link.download = 'sovereign-unverified-legacy-history.json';
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          } catch { setError('Legacy history export is unavailable.'); }
        }}>Export unverified legacy history</button>
      )}

      {sessions.length > 0 && (
        <div className="p-4 border-t border-slate-200">
          <button disabled={deleting} className="text-sm text-slate-500 hover:text-rose-600"
            onClick={async () => {
              if (!confirm('Delete all listed conversations from the server?')) return;
              const current = ++generation.current;
              setLoading(false);
              setDeleting(true);
              setError('');
              let failed = false;
              for (const session of sessions) {
                if (generation.current !== current) break;
                try {
                  await clearSession(session.id);
                  if (generation.current !== current) break;
                  setSessions(prev => prev.filter(item => item.id !== session.id));
                  if (session.id === currentSessionId) onNewChat();
                } catch { failed = true; }
              }
              if (failed) setError('Some conversations could not be deleted. They remain in the list.');
              setDeleting(false);
              onDeleted?.();
            }}>
            Clear All History
          </button>
        </div>
      )}
    </div>
  );
};
