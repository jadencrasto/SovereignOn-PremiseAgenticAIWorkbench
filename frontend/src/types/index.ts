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
}

export interface ChatRequestPayload {
  session_id?: string;
  message: string;
  model?: string;
  stream?: boolean;
  tools_enabled?: boolean;
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
  type: 'delta' | 'sources' | 'done' | 'error' | 'tool_start' | 'tool_result' | 'agent_status';
  content: string;
  session_id?: string;
  model_used?: string;
  sources?: SourceReference[];
  // Tool event fields
  tool?: string;
  tool_args?: Record<string, string>;
  success?: boolean;
  summary?: string;
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

export interface ModelsResponse {
  providers: Record<string, string[]>;
  default: string;
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

// Phase 4: Tool info from GET /api/tools
export interface ToolInfo {
  name: string;
  description: string;
  category: string;
  input_schema: Record<string, any>;
  read_only: boolean;
  requires_confirmation: boolean;
  enabled: boolean;
}

export interface ToolsListResponse {
  tools: ToolInfo[];
  total: number;
}

export type ActiveTab = 'chat' | 'documents' | 'models' | 'tools' | 'settings';
