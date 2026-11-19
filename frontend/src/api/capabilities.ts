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
type ModelReadiness = Capability & { id: string; expires_at?: number; identity_evidence?: 'provider_digest' | 'unavailable' };
export const localToolLabels = { calculator: 'Calculator', unit_conversion: 'Unit conversion' } as const;
export const localToolReasons = {
  available: 'Available',
  unavailable: 'Unavailable',
  disabled: 'Disabled',
  approval_required: 'Unavailable · approval required',
  confirmation_required: 'Unavailable · confirmation required',
} as const;
type LocalToolStatus = { available: boolean; reason: keyof typeof localToolReasons; requires_approval: boolean };
type LocalTools = { enabled: boolean; tools: Record<keyof typeof localToolLabels, LocalToolStatus> };

interface DocumentIntelligenceReport {
  formats: ['PDF', 'DOCX', 'XLSX'];
  extraction: 'offline_verified';
  reasoning: 'offline_verified';
  planning: 'offline_verified';
  direct_chat: 'blocked';
  artifact_publication: 'blocked';
  external_browser: 'unverified';
}

export interface CapabilityReport {
  schema_version: 1;
  environment: string;
  run_profile?: 'local_dev';
  history_retention?: 'disposable' | 'durable';
  local_tools?: LocalTools;
  local_knowledge?: { available: boolean; mode: 'bounded_lexical' };
  deployment_verified: false;
  capabilities: Record<CapabilityName, Capability>;
  models: Record<string, Capability & { id: string | null }>;
  installed_models: string[];
  selected_chat_model?: ModelReadiness;
  chat_models?: string[];
  selected_vision_model?: ModelReadiness;
  vision_models?: string[];
  text_file_understanding?: boolean;
  document_intelligence?: DocumentIntelligenceReport;
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
  local_text_chat_only: 'This profile supports text chat only.',
  component_not_initialized: 'A required backend component is not initialized.',
  prerequisites_observed: 'Required models were listed. Inference was not checked.',
  inference_not_checked: 'This installed model has not been checked for chat inference.',
  inference_check_in_progress: 'Checking chat inference.',
  inference_check_expired: 'The inference check expired. Check the model again.',
  inference_check_failed: 'The model inference check failed or timed out.',
  model_identity_changed: 'The model identity changed. Check inference again.',
  inference_verified: 'The backend completed a recent inference check for this model.',
  unsupported_chat_model: 'This model is not configured for chat.',
  unsupported_vision_model: 'Choose an installed model configured for vision in Models.',
  vision_model_digest_required: 'Ollama did not report an identity digest for this vision model. Check Ollama again.',
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
  if (!selectedModel || !report || !modelInInventory(report, selectedModel)) return true;
  const checked = image ? report.selected_vision_model : report.selected_chat_model;
  if (report.run_profile === 'local_dev' && !image) {
    return checked?.id !== selectedModel || checked.state !== 'available' ||
      checked.reason !== 'model_listed' || checked.identity_evidence !== 'provider_digest';
  }
  return !checked || checked.id !== selectedModel || checked.state !== 'available' ||
    (image && (!report.vision_models?.includes(selectedModel) || checked.identity_evidence !== 'provider_digest')) ||
    typeof checked.expires_at !== 'number' || !Number.isFinite(checked.expires_at) ||
    checked.expires_at <= Date.now() / 1000;
}

function isCapability(value: unknown): value is Capability {
  if (!value || typeof value !== 'object') return false;
  const { state, reason } = value as Partial<Capability>;
  return typeof reason === 'string' && ['available', 'unavailable', 'blocked', 'unknown'].includes(state ?? '');
}

function isModelReadiness(value: ModelReadiness | undefined, localChat = false): boolean {
  return !!value && isCapability(value) && typeof value.id === 'string' &&
    (value.state !== 'available' || (localChat
      ? value.reason === 'model_listed' && value.identity_evidence === 'provider_digest'
      : typeof value.expires_at === 'number' && Number.isFinite(value.expires_at)));
}

export function parseCapabilityReport(value: unknown): CapabilityReport {
  if (!value || typeof value !== 'object') throw new Error('Invalid capability response');
  const report = value as Partial<CapabilityReport>;
  if (report.schema_version !== 1 || report.deployment_verified !== false ||
      typeof report.environment !== 'string' ||
      (report.run_profile !== undefined && report.run_profile !== 'local_dev') ||
      (report.history_retention !== undefined && (report.run_profile !== 'local_dev' ||
        !['disposable', 'durable'].includes(report.history_retention))) ||
      (report.local_tools !== undefined && (report.run_profile !== 'local_dev' || !isLocalTools(report.local_tools))) ||
      (report.local_knowledge !== undefined && (report.run_profile !== 'local_dev' || !report.local_knowledge ||
        typeof report.local_knowledge.available !== 'boolean' || report.local_knowledge.mode !== 'bounded_lexical')) ||
      !report.capabilities || !Object.keys(capabilityLabels).every(key =>
        isCapability(report.capabilities?.[key as CapabilityName])) ||
      !report.models || !['default_chat', 'image_chat', 'vision', 'embedding'].every(key => {
        const model = report.models?.[key];
        return model && (model.id === null || typeof model.id === 'string') && isCapability(model);
      }) ||
      !Array.isArray(report.installed_models) || !report.installed_models.every(name => typeof name === 'string') ||
      !Array.isArray(report.chat_models) || !report.chat_models.every(name => typeof name === 'string') ||
      (report.document_intelligence !== undefined && !isDocumentIntelligence(report.document_intelligence)) ||
      !isModelReadiness(report.selected_chat_model, report.run_profile === 'local_dev') ||
      (report.vision_models !== undefined &&
        (!Array.isArray(report.vision_models) || !report.vision_models.every(name => typeof name === 'string'))) ||
      (report.selected_vision_model !== undefined && !isModelReadiness(report.selected_vision_model)) ||
      (report.text_file_understanding !== undefined && typeof report.text_file_understanding !== 'boolean')) {
    throw new Error('Invalid capability response');
  }
  return report as CapabilityReport;
}

function isLocalTools(value: LocalTools): boolean {
  const names = Object.keys(localToolLabels) as (keyof typeof localToolLabels)[];
  return !!value?.tools && Object.keys(value.tools).length === names.length &&
    names.every(name => {
      const tool = value.tools[name];
      return !!tool && Object.hasOwn(localToolReasons, tool.reason) &&
        tool.available === (tool.reason === 'available') &&
        typeof tool.requires_approval === 'boolean' &&
        (!tool.available || !tool.requires_approval) &&
        (tool.reason !== 'approval_required' || tool.requires_approval);
    }) && value.enabled === names.some(name => value.tools[name].available);
}

function isDocumentIntelligence(value: DocumentIntelligenceReport): boolean {
  return !!value && Array.isArray(value.formats) && value.formats.length === 3 &&
    value.formats[0] === 'PDF' && value.formats[1] === 'DOCX' && value.formats[2] === 'XLSX' &&
    value.extraction === 'offline_verified' && value.reasoning === 'offline_verified' &&
    value.planning === 'offline_verified' && value.direct_chat === 'blocked' &&
    value.artifact_publication === 'blocked' && value.external_browser === 'unverified';
}
