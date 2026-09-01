/**
 * frontend/src/components/chat/ChatHistorySidebar.tsx
 * ---------------------------------------------------
 * Full Persistent Chat History Management Drawer.
 * Stores multi-turn conversations in local storage, allows switching between past sessions,
 * continuing any chat anytime, searching history, renaming titles, and exporting transcripts.
 */

import React, { useState, useEffect, useMemo } from 'react';
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
  Sparkles,
  Layers,
} from 'lucide-react';

export interface ChatSession {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
  model: string;
}

const STORAGE_KEY = 'sovereign_chat_sessions_v2';

export function loadAllSessions(): ChatSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (err) {
    console.error('Failed to load chat history from localStorage', err);
    return [];
  }
}

export function saveSessionToStorage(session: ChatSession) {
  try {
    const existing = loadAllSessions();
    const index = existing.findIndex((s) => s.id === session.id);
    if (index >= 0) {
      existing[index] = session;
    } else {
      existing.unshift(session);
    }
    // Limit to last 50 sessions
    const trimmed = existing.slice(0, 50);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch (err) {
    console.error('Failed to save chat session', err);
  }
}

export function deleteSessionFromStorage(sessionId: string) {
  try {
    const existing = loadAllSessions().filter((s) => s.id !== sessionId);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(existing));
  } catch (err) {
    console.error('Failed to delete chat session', err);
  }
}

interface ChatHistorySidebarProps {
  isOpen: boolean;
  onClose: () => void;
  currentSessionId: string;
  onSelectSession: (session: ChatSession) => void;
  onNewChat: () => void;
}

export const ChatHistorySidebar: React.FC<ChatHistorySidebarProps> = ({
  isOpen,
  onClose,
  currentSessionId,
  onSelectSession,
  onNewChat,
}) => {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState<string>('');

  const reloadSessions = () => {
    setSessions(loadAllSessions());
  };

  useEffect(() => {
    if (isOpen) {
      reloadSessions();
    }
  }, [isOpen]);

  const filteredSessions = useMemo(() => {
    return sessions.filter((s) => {
      if (!searchQuery.trim()) return true;
      const q = searchQuery.toLowerCase();
      const matchTitle = s.title.toLowerCase().includes(q);
      const matchContent = s.messages.some((m) => m.content.toLowerCase().includes(q));
      return matchTitle || matchContent;
    });
  }, [sessions, searchQuery]);

  const handleDelete = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    deleteSessionFromStorage(id);
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (id === currentSessionId) {
      onNewChat();
    }
  };

  const handleStartRename = (e: React.MouseEvent, s: ChatSession) => {
    e.stopPropagation();
    setEditingId(s.id);
    setEditTitle(s.title);
  };

  const handleSaveRename = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (editTitle.trim()) {
      const updated = sessions.map((s) =>
        s.id === id ? { ...s, title: editTitle.trim(), updatedAt: Date.now() } : s
      );
      setSessions(updated);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
    }
    setEditingId(null);
  };

  const handleExportMarkdown = (e: React.MouseEvent, s: ChatSession) => {
    e.stopPropagation();
    let md = `# Sovereign AI Workbench — Chat Transcript\n`;
    md += `**Session:** ${s.title}\n`;
    md += `**Date:** ${new Date(s.createdAt).toLocaleString()}\n`;
    md += `**Model:** ${s.model}\n\n---\n\n`;

    for (const m of s.messages) {
      md += `### ${m.role === 'user' ? '👤 User' : '🤖 Sovereign Assistant'} (${m.timestamp})\n\n`;
      md += `${m.content}\n\n`;
      if (m.sources && m.sources.length > 0) {
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
    <div className="absolute inset-y-0 left-0 z-30 w-80 lg:w-96 bg-[#0c1322] border-r border-slate-800 flex flex-col shadow-2xl backdrop-blur-xl animate-in slide-in-from-left duration-200">
      {/* Top Header */}
      <div className="p-4 border-b border-slate-800 flex items-center justify-between gap-2 bg-slate-900/60">
        <div className="flex items-center gap-2">
          <Clock className="w-4 h-4 text-sky-400" />
          <h2 className="text-sm font-bold text-white tracking-tight">Chat History</h2>
          <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700">
            {sessions.length} sessions
          </span>
        </div>

        <button
          onClick={onClose}
          className="p-1 text-slate-400 hover:text-white rounded hover:bg-slate-800 transition-colors"
          title="Close History"
        >
          <ChevronLeft className="w-5 h-5" />
        </button>
      </div>

      {/* New Conversation Button */}
      <div className="p-3 border-b border-slate-800/80">
        <button
          onClick={() => {
            onNewChat();
            onClose();
          }}
          className="w-full py-2 px-3.5 bg-gradient-to-r from-sky-600 to-indigo-600 hover:from-sky-500 hover:to-indigo-500 text-white rounded-lg font-semibold text-xs flex items-center justify-center gap-2 shadow-md shadow-sky-600/20 transition-all"
        >
          <Plus className="w-4 h-4" />
          <span>New Conversation</span>
        </button>
      </div>

      {/* Search Input */}
      <div className="px-3 pt-3 pb-2">
        <div className="relative">
          <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search past conversations..."
            className="w-full pl-8 pr-3 py-1.5 bg-slate-900/90 border border-slate-800 rounded-md text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-sky-500 font-sans"
          />
        </div>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {filteredSessions.length === 0 ? (
          <div className="py-12 px-4 text-center text-slate-500">
            <MessageSquare className="w-8 h-8 mx-auto mb-2 opacity-30 text-slate-400" />
            <p className="text-xs font-medium text-slate-400">No chat history found</p>
            <p className="text-[11px] text-slate-600 mt-1">
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
                onClick={() => {
                  if (!isEditing) {
                    onSelectSession(s);
                    onClose();
                  }
                }}
                className={`group p-3 rounded-lg border transition-all cursor-pointer relative ${
                  isCurrent
                    ? 'bg-sky-950/40 border-sky-500/40 shadow-sm shadow-sky-500/10'
                    : 'bg-slate-900/60 hover:bg-slate-850 border-slate-800/80 hover:border-slate-700'
                }`}
              >
                {/* Title and Edit Field */}
                <div className="flex items-start justify-between gap-2 mb-1.5">
                  {isEditing ? (
                    <div className="flex items-center gap-1 flex-1">
                      <input
                        type="text"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                        className="flex-1 bg-slate-950 border border-sky-500 rounded px-2 py-0.5 text-xs text-white focus:outline-none"
                        autoFocus
                      />
                      <button
                        onClick={(e) => handleSaveRename(e, s.id)}
                        className="p-1 text-emerald-400 hover:bg-slate-800 rounded"
                        title="Save"
                      >
                        <Check className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditingId(null);
                        }}
                        className="p-1 text-slate-400 hover:bg-slate-800 rounded"
                        title="Cancel"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ) : (
                    <h3 className={`text-xs font-semibold tracking-tight truncate flex-1 ${isCurrent ? 'text-sky-300' : 'text-slate-200'}`}>
                      {s.title}
                    </h3>
                  )}

                  {/* Actions (Rename, Export, Delete) */}
                  {!isEditing && (
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        onClick={(e) => handleStartRename(e, s)}
                        className="p-1 text-slate-400 hover:text-white hover:bg-slate-800 rounded"
                        title="Rename"
                      >
                        <Edit2 className="w-3 h-3" />
                      </button>
                      <button
                        onClick={(e) => handleExportMarkdown(e, s)}
                        className="p-1 text-slate-400 hover:text-sky-400 hover:bg-slate-800 rounded"
                        title="Export Markdown"
                      >
                        <Download className="w-3 h-3" />
                      </button>
                      <button
                        onClick={(e) => handleDelete(e, s.id)}
                        className="p-1 text-slate-400 hover:text-rose-400 hover:bg-slate-800 rounded"
                        title="Delete"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  )}
                </div>

                {/* Subtitle Telemetry */}
                <div className="flex items-center justify-between text-[10.5px] text-slate-400">
                  <span className="flex items-center gap-1">
                    <Calendar className="w-3 h-3 text-slate-500" />
                    <span>{formatRelativeTime(s.updatedAt)}</span>
                  </span>
                  <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-slate-800/80 border border-slate-700 text-slate-300">
                    {s.messages.length} msgs
                  </span>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Footer Clear All */}
      {sessions.length > 0 && (
        <div className="p-3 border-t border-slate-800/80 bg-slate-950/50 flex items-center justify-between text-xs">
          <button
            onClick={() => {
              if (confirm('Clear all conversation history? This cannot be undone.')) {
                localStorage.removeItem(STORAGE_KEY);
                setSessions([]);
                onNewChat();
              }
            }}
            className="text-[11px] text-slate-400 hover:text-rose-400 transition-colors flex items-center gap-1"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>Clear All History</span>
          </button>
        </div>
      )}
    </div>
  );
};
