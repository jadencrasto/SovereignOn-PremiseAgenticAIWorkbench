import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import type { ReactNode } from 'react';
import type {
  ActiveTab,
  HealthResponse,
  DocumentItem,
} from '../types';
import { checkHealth } from '../api/health';
import { fetchModels } from '../api/models';
import { fetchDocuments } from '../api/documents';

interface Toast {
  id: string;
  type: 'success' | 'error' | 'info';
  message: string;
}

interface WorkbenchContextType {
  activeTab: ActiveTab;
  setActiveTab: (tab: ActiveTab) => void;
  health: HealthResponse | null;
  isBackendConnected: boolean;
  isCheckingHealth: boolean;
  refreshHealth: () => Promise<void>;
  
  // Model state
  availableModels: string[];
  defaultModel: string;
  selectedModel: string;
  setSelectedModel: (model: string) => void;
  refreshModels: () => Promise<void>;

  // Document state
  documents: DocumentItem[];
  isDocsLoading: boolean;
  refreshDocuments: () => Promise<void>;

  // Session state
  sessionId: string;
  resetSession: () => void;

  // Toast notifications
  toasts: Toast[];
  addToast: (type: 'success' | 'error' | 'info', message: string) => void;
  removeToast: (id: string) => void;
}

const WorkbenchContext = createContext<WorkbenchContextType | undefined>(undefined);

function generateUUID(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return 'sess_' + Math.random().toString(36).substring(2, 11);
}

export const WorkbenchProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [activeTab, setActiveTab] = useState<ActiveTab>('chat');
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [isBackendConnected, setIsBackendConnected] = useState<boolean>(false);
  const [isCheckingHealth, setIsCheckingHealth] = useState<boolean>(true);

  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [defaultModel, setDefaultModel] = useState<string>('ollama/qwen2.5:7b');
  const [selectedModel, setSelectedModel] = useState<string>('ollama/qwen2.5:7b');

  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [isDocsLoading, setIsDocsLoading] = useState<boolean>(false);

  const [sessionId, setSessionId] = useState<string>(generateUUID());
  const [toasts, setToasts] = useState<Toast[]>([]);

  const addToast = useCallback((type: 'success' | 'error' | 'info', message: string) => {
    const id = Math.random().toString(36).substring(2, 9);
    setToasts((prev) => [...prev, { id, type, message }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const refreshHealth = useCallback(async () => {
    setIsCheckingHealth(true);
    try {
      const data = await checkHealth();
      setHealth(data);
      setIsBackendConnected(data.status === 'ok');
    } catch {
      setIsBackendConnected(false);
      setHealth(null);
    } finally {
      setIsCheckingHealth(false);
    }
  }, []);

  const refreshModels = useCallback(async () => {
    try {
      const data = await fetchModels();
      const models: string[] = [];
      if (data.providers) {
        Object.entries(data.providers).forEach(([provider, list]) => {
          list.forEach((m) => models.push(`${provider}/${m}`));
        });
      }
      setAvailableModels(models);
      if (data.default) {
        setDefaultModel(data.default);
        setSelectedModel((prev) => (models.includes(prev) ? prev : data.default));
      }
    } catch (err) {
      console.warn('Failed to load models list:', err);
    }
  }, []);

  const refreshDocuments = useCallback(async () => {
    setIsDocsLoading(true);
    try {
      const res = await fetchDocuments();
      setDocuments(res.documents || []);
    } catch (err) {
      console.warn('Failed to load documents:', err);
    } finally {
      setIsDocsLoading(false);
    }
  }, []);

  const resetSession = useCallback(() => {
    setSessionId(generateUUID());
    addToast('info', 'New conversation session started.');
  }, [addToast]);

  useEffect(() => {
    refreshHealth();
    refreshModels();
    refreshDocuments();

    const interval = setInterval(() => {
      refreshHealth();
    }, 15000);

    return () => clearInterval(interval);
  }, [refreshHealth, refreshModels, refreshDocuments]);

  return (
    <WorkbenchContext.Provider
      value={{
        activeTab,
        setActiveTab,
        health,
        isBackendConnected,
        isCheckingHealth,
        refreshHealth,
        availableModels,
        defaultModel,
        selectedModel,
        setSelectedModel,
        refreshModels,
        documents,
        isDocsLoading,
        refreshDocuments,
        sessionId,
        resetSession,
        toasts,
        addToast,
        removeToast,
      }}
    >
      {children}
    </WorkbenchContext.Provider>
  );
};

export function useWorkbench(): WorkbenchContextType {
  const context = useContext(WorkbenchContext);
  if (!context) {
    throw new Error('useWorkbench must be used within a WorkbenchProvider');
  }
  return context;
}
