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

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
  model_used?: string;
  sources?: SourceReference[];
  isStreaming?: boolean;
  error?: boolean;
}

export interface ChatRequestPayload {
  session_id?: string;
  message: string;
  model?: string;
  stream?: boolean;
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
  type: 'delta' | 'sources' | 'done' | 'error';
  content: string;
  session_id?: string;
  model_used?: string;
  sources?: SourceReference[];
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

export type ActiveTab = 'chat' | 'documents' | 'models' | 'tools' | 'settings';
