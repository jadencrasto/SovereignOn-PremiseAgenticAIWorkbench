import React, { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import type { ReactNode } from 'react';
import type {
  ActiveTab,
  HealthResponse,
  DocumentItem,
} from '../types';
import { checkHealth, fetchCapabilities, checkModelInference, checkVisionInference } from '../api/health';
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
  runProfile?: CapabilityReport['run_profile'];
  historyRetention?: CapabilityReport['history_retention'];
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
  selectedVisionModel: string;
  setSelectedVisionModel: (model: string) => void;
  checkSelectedVision: () => Promise<void>;

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

function withoutReadiness(report: CapabilityReport, kind: 'chat' | 'vision', id: string, reason: string): CapabilityReport {
  const status = { state: 'unknown' as const, reason };
  return kind === 'chat' ? {
    ...report, selected_chat_model: { id, ...status },
    capabilities: { ...report.capabilities, chat: status,
      planning: report.capabilities.planning.state === 'available' ? status : report.capabilities.planning,
      multimodal: report.capabilities.multimodal.state === 'available' ? status : report.capabilities.multimodal },
  } : {
    ...report, selected_vision_model: { id, ...status },
    capabilities: { ...report.capabilities, multimodal: status },
  };
}

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
  const [runProfile, setRunProfile] = useState<CapabilityReport['run_profile']>();
  const [historyRetention, setHistoryRetention] = useState<CapabilityReport['history_retention']>();
  const isBackendConnected = permitsCapability(capabilities, "backend");
  const healthRequest = useRef(false);
  const [isCheckingHealth, setIsCheckingHealth] = useState<boolean>(true);

  const defaultModel = capabilities?.models.default_chat.id ?? '';
  const availableModels = capabilities?.chat_models ?? [];
  const [modelSelection, setSelectedModel] = useState('');
  const [visionSelection, setSelectedVisionModel] = useState('');
  const selectedModel = modelSelection || defaultModel;
  const selectedVisionModel = visionSelection || capabilities?.models.vision?.id || '';
  let presentedCapabilities = capabilities;
  if (presentedCapabilities && presentedCapabilities.selected_chat_model?.id !== selectedModel) {
    presentedCapabilities = withoutReadiness(presentedCapabilities, 'chat', selectedModel, 'inference_not_checked');
  }
  if (presentedCapabilities && presentedCapabilities.selected_vision_model?.id !== selectedVisionModel) {
    presentedCapabilities = withoutReadiness(presentedCapabilities, 'vision', selectedVisionModel, 'inference_not_checked');
  }

  useEffect(() => {
    const timers = (['chat', 'vision'] as const).flatMap(kind => {
      const key = kind === 'chat' ? 'selected_chat_model' : 'selected_vision_model';
      const checked = capabilities?.[key];
      if (checked?.state !== 'available' || typeof checked.expires_at !== 'number') return [];
      return [setTimeout(() => setCapabilities(previous => previous && previous[key] === checked
        ? withoutReadiness(previous, kind, checked.id, 'inference_check_expired') : previous),
        Math.max(0, Math.ceil(checked.expires_at * 1000 - Date.now())))];
    });
    return () => timers.forEach(timer => clearTimeout(timer));
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
      const report = await fetchCapabilities(checkOllama, selectedModel, visionSelection);
      setRunProfile(report.run_profile);
      setHistoryRetention(report.history_retention);
      setCapabilities(report);
    } catch {
      setCapabilities(null);
      if (checkOllama) addToast('error', 'The Ollama check could not be completed. Retry when the backend is ready.');
    } finally {
      healthRequest.current = false;
      setIsCheckingHealth(false);
    }
  }, [addToast, selectedModel, visionSelection]);

  const refreshHealth = useCallback(async () => {
    await refreshCapabilities();
    try {
      setHealth(await checkHealth());
    } catch {
      setHealth(null);
    }
  }, [refreshCapabilities]);

  const refreshModels = useCallback(() => refreshCapabilities(true), [refreshCapabilities]);

  const checkSelectedInference = useCallback(async (kind: 'chat' | 'vision') => {
    const model = kind === 'chat' ? selectedModel : selectedVisionModel;
    if (healthRequest.current || !model) return;
    healthRequest.current = true;
    setIsCheckingHealth(true);
    // Keep the inventory and selected model visible, but revoke the old
    // eligibility while the server is checking the selected model again.
    setCapabilities(previous => previous && withoutReadiness(previous, kind, model, 'inference_check_in_progress'));
    try {
      setCapabilities(await (kind === 'chat' ? checkModelInference(model, visionSelection)
        : checkVisionInference(model, selectedModel)));
    } catch {
      setCapabilities(previous => previous && withoutReadiness(previous, kind, model, 'inference_check_failed'));
      addToast('error', `The ${kind} inference check could not be completed. Check inventory and retry.`);
    } finally {
      healthRequest.current = false;
      setIsCheckingHealth(false);
    }
  }, [selectedModel, selectedVisionModel, visionSelection, addToast]);
  const checkSelectedModel = useCallback(() => checkSelectedInference('chat'), [checkSelectedInference]);
  const checkSelectedVision = useCallback(() => checkSelectedInference('vision'), [checkSelectedInference]);

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
        runProfile,
        historyRetention,
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
        selectedVisionModel,
        setSelectedVisionModel,
        checkSelectedVision,
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
