import React, { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import type { ReactNode } from 'react';
import type {
  ActiveTab,
  HealthResponse,
  DocumentItem,
} from '../types';
import { checkHealth, fetchCapabilities, checkModelInference } from '../api/health';
import { permitsCapability, type CapabilityReport } from '../api/capabilities';
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
  capabilities: CapabilityReport | null;
  isBackendConnected: boolean;
  refreshCapabilities: (checkOllama?: boolean) => Promise<void>;
  isCheckingHealth: boolean;
  refreshHealth: () => Promise<void>;
  
  // Model state
  availableModels: string[];
  defaultModel: string;
  selectedModel: string;
  setSelectedModel: (model: string) => void;
  refreshModels: () => Promise<void>;
  checkSelectedModel: () => Promise<void>;

  // Document state
  documents: DocumentItem[];
  isDocsLoading: boolean;
  documentsError: string | null;
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
  const [capabilities, setCapabilities] = useState<CapabilityReport | null>(null);
  const isBackendConnected = permitsCapability(capabilities, "backend");
  const healthRequest = useRef(false);
  const [isCheckingHealth, setIsCheckingHealth] = useState<boolean>(true);

  const defaultModel = capabilities?.models.default_chat.id ?? '';
  const availableModels = capabilities?.chat_models ?? [];
  const [modelSelection, setSelectedModel] = useState('');
  const selectedModel = modelSelection || defaultModel;
  const presentedCapabilities = capabilities && capabilities.selected_chat_model?.id !== selectedModel
    ? { ...capabilities, capabilities: { ...capabilities.capabilities,
        chat: { state: 'unknown' as const, reason: 'inference_not_checked' },
        planning: { state: 'unknown' as const, reason: 'inference_not_checked' } },
        selected_chat_model: { id: selectedModel, state: 'unknown' as const, reason: 'inference_not_checked' } }
    : capabilities;

  useEffect(() => {
    const checked = capabilities?.selected_chat_model;
    if (checked?.state !== 'available' || typeof checked.expires_at !== 'number') return;
    const expiry = checked.expires_at;
    const timer = setTimeout(() => setCapabilities(previous =>
      previous?.selected_chat_model?.expires_at === expiry ? null : previous),
      Math.max(0, expiry * 1000 - Date.now()));
    return () => clearTimeout(timer);
  }, [capabilities]);

  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [isDocsLoading, setIsDocsLoading] = useState<boolean>(false);
  const [documentsError, setDocumentsError] = useState<string | null>(null);
  const documentsRequest = useRef(0);
  const documentAvailable = permitsCapability(capabilities, 'document_rag');

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

  const refreshCapabilities = useCallback(async (checkOllama = false) => {
    if (healthRequest.current) return;
    healthRequest.current = true;
    setIsCheckingHealth(true);
    try {
      setCapabilities(await fetchCapabilities(checkOllama, selectedModel));
    } catch {
      setCapabilities(null);
      if (checkOllama) addToast('error', 'The Ollama check could not be completed. Retry when the backend is ready.');
    } finally {
      healthRequest.current = false;
      setIsCheckingHealth(false);
    }
  }, [addToast, selectedModel]);

  const refreshHealth = useCallback(async () => {
    await refreshCapabilities();
    try {
      setHealth(await checkHealth());
    } catch {
      setHealth(null);
    }
  }, [refreshCapabilities]);

  const refreshModels = useCallback(() => refreshCapabilities(true), [refreshCapabilities]);

  const checkSelectedModel = useCallback(async () => {
    if (healthRequest.current || !selectedModel) return;
    healthRequest.current = true;
    setIsCheckingHealth(true);
    // Keep the inventory and selected model visible, but revoke the old
    // eligibility while the server is checking the selected model again.
    setCapabilities(previous => previous ? {
      ...previous,
      capabilities: { ...previous.capabilities,
        chat: { state: 'unknown', reason: 'inference_check_in_progress' },
        planning: { state: 'unknown', reason: 'inference_check_in_progress' } },
      selected_chat_model: { id: selectedModel, state: 'unknown', reason: 'inference_check_in_progress' },
    } : null);
    try {
      setCapabilities(await checkModelInference(selectedModel));
    } catch {
      setCapabilities(null);
      addToast('error', 'The model inference check could not be completed. Check inventory and retry.');
    } finally {
      healthRequest.current = false;
      setIsCheckingHealth(false);
    }
  }, [selectedModel, addToast]);

  const refreshDocuments = useCallback(async () => {
    const request = ++documentsRequest.current;
    if (!documentAvailable) {
      setDocuments([]);
      setDocumentsError(null);
      setIsDocsLoading(false);
      return;
    }
    setIsDocsLoading(true);
    setDocumentsError(null);
    try {
      const res = await fetchDocuments();
      if (request === documentsRequest.current) setDocuments(res.documents);
    } catch {
      if (request === documentsRequest.current) {
        setDocuments([]);
        setDocumentsError('Document listing is unavailable. Please try again.');
      }
    } finally {
      if (request === documentsRequest.current) setIsDocsLoading(false);
    }
  }, [documentAvailable]);

  useEffect(() => {
    void refreshDocuments();
    return () => { documentsRequest.current++; };
  }, [refreshDocuments]);

  const resetSession = useCallback(() => {
    setSessionId(generateUUID());
    addToast('info', 'New conversation session started.');
  }, [addToast]);

  useEffect(() => {
    refreshHealth();

    const interval = setInterval(() => {
      refreshHealth();
    }, 15000);

    return () => clearInterval(interval);
  }, [refreshHealth]);

  return (
    <WorkbenchContext.Provider
      value={{
        activeTab,
        setActiveTab,
        health,
        capabilities: presentedCapabilities,
        refreshCapabilities,
        isBackendConnected,
        isCheckingHealth,
        refreshHealth,
        availableModels,
        defaultModel,
        selectedModel,
        setSelectedModel,
        refreshModels,
        checkSelectedModel,
        documents,
        isDocsLoading,
        documentsError,
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
