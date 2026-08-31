export type MessageRole = 'system' | 'user' | 'assistant';

export interface SourceReference {
  document_id: string;
  filename: string;
  chunk_id: string;
  chunk_index: number;
  page?: number | null;
  score: number;
  file_type?: string;
}

// Phase 4: Tool event types
export interface ToolEvent {
  type: 'tool_start' | 'tool_result';
  tool: string;
  arguments?: Record<string, string>;
  success?: boolean;
  summary?: string;
}

// Phase 5: Image attachment (client-side, no base64 in API responses)
export interface ImageAttachment {
  /** UUID assigned by backend after successful upload */
  id: string;
  /** Original sanitized filename */
  filename: string;
  /** MIME type, e.g. 'image/png' */
  mimeType: string;
  /** File size in bytes */
  sizeBytes: number;
  /** Width in pixels (from backend, if available) */
  width?: number | null;
  /** Height in pixels (from backend, if available) */
  height?: number | null;
  /**
   * Browser-local object URL created from File.
   * Used for thumbnail display — revoked when no longer needed.
   * NEVER sent to backend; never stored in the database.
   */
  objectUrl: string;
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
  model_used?: string;
  sources?: SourceReference[];
  toolEvents?: ToolEvent[];
  isStreaming?: boolean;
  error?: boolean;
  // Phase 5: optional image attachments on user messages
  attachments?: ImageAttachment[];
  // Phase 6: Plan and Approval states
  plan?: {
    taskId: string;
    objective: string;
    steps: PlanStep[];
  };
  pendingApproval?: ApprovalRequest;
}

export interface ChatRequestPayload {
  session_id?: string;
  message: string;
  model?: string;
  stream?: boolean;
  tools_enabled?: boolean;
}

// Phase 5: Multimodal request payload (sent as FormData)
export interface MultimodalChatRequestPayload {
  session_id?: string;
  message: string;
  model?: string;
  stream?: boolean;
  tools_enabled?: boolean;
  image?: File;  // actual File object to be included in FormData
}

export interface ChatResponsePayload {
  session_id: string;
  message: {
    role: MessageRole;
    content: string;
  };
  model_used: string;
  sources?: SourceReference[] | null;
}

export interface StreamChunkPayload {
  type: 'delta' | 'sources' | 'done' | 'error' | 'tool_start' | 'tool_result' | 'agent_status'
    | 'plan_created' | 'plan_step' | 'approval_required' | 'approval_granted'
    | 'approval_rejected' | 'task_started' | 'task_completed' | 'task_failed' | 'task_cancelled';
  content: string;
  session_id?: string;
  model_used?: string;
  sources?: SourceReference[];
  // Tool event fields
  tool?: string;
  tool_args?: Record<string, string>;
  success?: boolean;
  summary?: string;
  // Phase 5: attachment metadata in done event
  attachment?: {
    attachment_id: string;
    filename: string;
    mime_type: string;
    size_bytes: number;
    width?: number | null;
    height?: number | null;
  };
  // Phase 6: planning/approval event data
  task_id?: string;
  plan?: {
    objective: string;
    steps: Array<{
      id: string;
      description: string;
      tool_name?: string | null;
      requires_approval: boolean;
      status: string;
    }>;
  };
  step_id?: string;
  approval_id?: string;
  risk_level?: string;
  reason?: string;
  expires_at?: string;
  steps_completed?: number;
  error?: string;
}

export interface DocumentItem {
  document_id: string;
  filename: string;
  file_type: string;
  chunk_count: number;
}

export interface DocumentListResponse {
  documents: DocumentItem[];
  total: number;
}

export interface DocumentUploadResponse {
  document_id: string;
  filename: string;
  file_type: string;
  chunks: number;
  status: string;
}

export interface DocumentDeleteResponse {
  document_id: string;
  chunks_deleted: number;
  status: string;
}

export interface DocumentChunkItem {
  chunk_id: string;
  chunk_index: number;
  page?: number | null;
  text: string;
  metadata?: Record<string, any>;
}

export interface DocumentDetailResponse {
  document_id: string;
  filename: string;
  file_type: string;
  chunk_count: number;
  relative_path?: string | null;
  chunks: DocumentChunkItem[];
}

// Phase 5: Extended models response with capability metadata
export interface ModelInfo {
  id: string;
  name: string;
  provider: string;
  capabilities: string[];  // e.g. ['chat', 'vision'] or ['embedding']
  description?: string;
  installed: boolean;
}

export interface ModelsResponse {
  providers: Record<string, string[]>;
  default: string;
  // Phase 5 additions (optional, for backward compat)
  models?: ModelInfo[];
  capability_routing?: {
    chat?: string;
    vision?: string;
    embedding?: string;
  };
}

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  environment: string;
  model_provider: string;
  default_model: string;
  ollama_url: string;
}

export interface ToolInfo {
  name: string;
  description: string;
  category: string;
  input_schema: Record<string, any>;
  read_only: boolean;
  requires_confirmation: boolean;
  requires_approval: boolean;   // Phase 6
  risk_level: string;           // Phase 6: 'low' | 'medium' | 'high'
  enabled: boolean;
}

export interface ToolsListResponse {
  tools: ToolInfo[];
  total: number;
}

export type ActiveTab =
  | 'chat'
  | 'demo'
  | 'graph'
  | 'tasks'
  | 'artifacts'
  | 'documents'
  | 'models'
  | 'tools'
  | 'audit'
  | 'health'
  | 'security'
  | 'settings';



// Phase 5: Allowed image MIME types for client-side validation
export const ALLOWED_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp'] as const;
export const ALLOWED_IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp'] as const;
export const MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024; // 10 MB

// Phase 6: Plan / Task / Approval types
export interface PlanStep {
  id: string;
  description: string;
  tool_name?: string | null;
  arguments?: Record<string, any>;
  requires_approval: boolean;
  status: string; // 'pending' | 'awaiting_approval' | 'approved' | 'running' | 'completed' | 'failed' | 'skipped'
  result?: string | null;
  error?: string | null;
}

export interface AgentPlan {
  task_id: string;
  objective: string;
  steps: PlanStep[];
  status: string;
  created_at: string;
}

export interface ApprovalRequest {
  approval_id: string;
  task_id: string;
  step_id: string;
  tool_name: string;
  arguments_hash: string;
  risk_level: string;
  reason: string;
  status: string; // 'pending' | 'approved' | 'rejected' | 'expired'
  created_at: string;
  expires_at: string;
  resolved_at?: string | null;
}

export interface TaskSummary {
  task_id: string;
  session_id: string;
  user_request: string;
  status: string;
  step_count: number;
  completed_steps: number;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
}

export interface TaskDetail {
  task_id: string;
  session_id: string;
  user_request: string;
  plan?: AgentPlan | null;
  current_step_idx: number;
  status: string;
  result?: string | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
}

export interface TaskListResponse {
  tasks: TaskSummary[];
  total: number;
}

// ---------------------------------------------------------------------------
// Phase 7: Authentication, RBAC, Audit & Observability Types
// ---------------------------------------------------------------------------

export type UserRole = 'admin' | 'operator' | 'viewer';

export interface User {
  id: string;
  username: string;
  role: UserRole;
  is_active: boolean;
  must_change_password: boolean;
  created_at: string;
  last_login_at?: string | null;
}

export interface AuditEvent {
  event_id: string;
  timestamp: string;
  session_id?: string | null;
  user_id?: string | null;
  role?: string | null;
  event_type: string;
  action?: string | null;
  resource?: string | null;
  tool?: string | null;
  task_id?: string | null;
  step_id?: string | null;
  success: boolean;
  duration_ms?: number | null;
  metadata: Record<string, any>;
  failure_reason?: string | null;
  request_id?: string | null;
}

export interface AuditSummary {
  total_events: number;
  failed_events: number;
  denied_actions: number;
  tool_executions: number;
  auth_failures: number;
}

export interface AuditListResponse {
  events: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface SecurityDiagnosticItem {
  id: string;
  category: string;
  title: string;
  status: 'PASS' | 'WARN' | 'FAIL';
  details: string;
  remediation?: string;
}

export interface SecurityStatusResponse {
  overall_status: 'PASS' | 'WARN' | 'FAIL';
  diagnostics: SecurityDiagnosticItem[];
}

export interface ComponentHealth {
  name: string;
  status: 'healthy' | 'degraded' | 'unhealthy';
  details: string;
  latency_ms?: number | null;
}

export interface ReadinessResponse {
  ready: boolean;
  status: string;
  components: ComponentHealth[];
  cached: boolean;
}

export interface TaskMonitoringSummary {
  total_tasks: number;
  stale_tasks: number;
  counts_by_status: Record<string, number>;
  grouped_tasks: Record<string, any[]>;
}
