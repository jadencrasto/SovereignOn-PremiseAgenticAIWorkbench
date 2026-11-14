export const capabilityLabels = {
  backend: 'Backend',
  ollama: 'Ollama inventory',
  chat: 'Text chat',
  planning: 'Agent planning',
  conversation_persistence: 'Conversation persistence',
  multimodal: 'Image chat',
  document_rag: 'Document ingestion and retrieval',
  approval_resume: 'Task approval and resume',
  artifact_access: 'Artifact access',
  code_execution: 'Generated code execution',
} as const;

export type CapabilityName = keyof typeof capabilityLabels;
export type CapabilityState = 'available' | 'unavailable' | 'blocked' | 'unknown';
export type Capability = { state: CapabilityState; reason: string };

export interface CapabilityReport {
  schema_version: 1;
  environment: string;
  deployment_verified: false;
  capabilities: Record<CapabilityName, Capability>;
  models: Record<string, Capability & { id: string | null }>;
  installed_models: string[];
  selected_chat_model?: Capability & { id: string; expires_at?: number; identity_evidence?: 'provider_digest' | 'unavailable' };
  chat_models?: string[];
}

const reasons: Record<string, string> = {
  startup_complete: 'Backend startup completed.',
  startup_incomplete: 'Backend startup has not completed.',
  backend_unreachable: 'The readiness endpoint did not respond.',
  status_pending: 'Waiting for the backend status.',
  model_check_required: 'Run Check Ollama to verify the configured local model inventory.',
  loopback_required: 'The readiness check requires a plain HTTP loopback endpoint.',
  model_not_configured: 'A required model is not configured.',
  provider_not_checked: 'This provider is outside the local inventory check.',
  inventory_observed: 'Ollama returned its model inventory.',
  invalid_model_inventory: 'Ollama returned an invalid or oversized inventory.',
  ollama_check_failed: 'Could not read the local Ollama inventory.',
  configured_model_missing: 'The configured model was not listed by Ollama.',
  model_listed: 'The configured model was listed by Ollama.',
  component_not_initialized: 'A required backend component is not initialized.',
  prerequisites_observed: 'Required models were listed. Inference was not checked.',
  inference_not_checked: 'This installed model has not been checked for chat inference.',
  inference_check_in_progress: 'Checking chat inference.',
  inference_check_expired: 'The inference check expired. Check the model again.',
  inference_check_failed: 'The model inference check failed or timed out.',
  model_identity_changed: 'The model identity changed. Check inference again.',
  inference_verified: 'A recent synthetic inference check completed. Live operation remains unverified.',
  unsupported_chat_model: 'This model is not configured for chat.',
  persistence_initialized_unverified: 'The store initialized. Durable writes and recovery were not checked.',
  protected_document_storage_unverified: 'Protected document storage and index integrity are unverified.',
  task_revalidation_unavailable: 'Task authorization cannot be safely revalidated.',
  artifact_provenance_unverified: 'Artifact provenance cannot be safely revalidated.',
  execution_isolation_unverified: 'Generated code execution isolation is unverified.',
};

export function describeCapability(capability: Capability): string {
  return reasons[capability.reason] ?? 'No verified status detail is available.';
}

export function capabilityFor(report: CapabilityReport | null, name: CapabilityName, offline = false): Capability {
  return report?.capabilities[name] ?? (offline && name === 'backend'
    ? { state: 'unavailable', reason: 'backend_unreachable' }
    : { state: 'unknown', reason: offline ? 'backend_unreachable' : 'status_pending' });
}

export function permitsCapability(report: CapabilityReport | null, name: CapabilityName): boolean {
  return capabilityFor(report, name).state === 'available';
}

// Match the backend's Ollama inventory normalization for implicit :latest tags.
export function modelInInventory(report: CapabilityReport, selectedModel: string): boolean {
  if (!selectedModel.startsWith('ollama/')) return false;
  const normalize = (name: string) => name.includes(':') && name.lastIndexOf(':') > name.lastIndexOf('/')
    ? name : `${name}:latest`;
  const selected = normalize(selectedModel.slice('ollama/'.length));
  return report.installed_models.some(model => normalize(model) === selected);
}

// An unknown capability is not evidence that a request can be served.
export function chatDisabled(report: CapabilityReport | null, image = false, selectedModel?: string): boolean {
  if (!permitsCapability(report, 'backend')) return true;
  const capability = capabilityFor(report, image ? 'multimodal' : 'chat');
  // A model inventory cannot override a blocked or unavailable chat capability.
  if (capability.state !== 'available') return true;
  if (!image) {
    const checked = report?.selected_chat_model;
    if (!selectedModel || !checked || checked.id !== selectedModel || checked.state !== 'available' ||
        typeof checked.expires_at !== 'number' || checked.expires_at * 1000 <= Date.now()) return true;
  }
  return false;
}

function isCapability(value: unknown): value is Capability {
  if (!value || typeof value !== 'object') return false;
  const { state, reason } = value as Partial<Capability>;
  return typeof reason === 'string' && ['available', 'unavailable', 'blocked', 'unknown'].includes(state ?? '');
}

export function parseCapabilityReport(value: unknown): CapabilityReport {
  if (!value || typeof value !== 'object') throw new Error('Invalid capability response');
  const report = value as Partial<CapabilityReport>;
  if (report.schema_version !== 1 || report.deployment_verified !== false ||
      typeof report.environment !== 'string' ||
      !report.capabilities || !Object.keys(capabilityLabels).every(key =>
        isCapability(report.capabilities?.[key as CapabilityName])) ||
      !report.models || !['default_chat', 'image_chat', 'vision', 'embedding'].every(key => {
        const model = report.models?.[key];
        return model && (model.id === null || typeof model.id === 'string') && isCapability(model);
      }) ||
      !Array.isArray(report.installed_models) || !report.installed_models.every(name => typeof name === 'string') ||
      !Array.isArray(report.chat_models) || !report.chat_models.every(name => typeof name === 'string') ||
      !report.selected_chat_model || !isCapability(report.selected_chat_model) ||
      typeof report.selected_chat_model.id !== 'string' ||
      (report.selected_chat_model.state === 'available' &&
        (typeof report.selected_chat_model.expires_at !== 'number' || !Number.isFinite(report.selected_chat_model.expires_at)))) {
    throw new Error('Invalid capability response');
  }
  return report as CapabilityReport;
}
