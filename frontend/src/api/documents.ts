import { apiRequest } from './client';
import type {
  DocumentListResponse,
  DocumentUploadResponse,
  DocumentDeleteResponse,
} from '../types';

export async function fetchDocuments(): Promise<DocumentListResponse> {
  return apiRequest<DocumentListResponse>('/api/documents');
}

export async function uploadDocument(file: File): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  return apiRequest<DocumentUploadResponse>('/api/documents', {
    method: 'POST',
    body: formData,
  });
}

export async function deleteDocument(documentId: string): Promise<DocumentDeleteResponse> {
  return apiRequest<DocumentDeleteResponse>(`/api/documents/${encodeURIComponent(documentId)}`, {
    method: 'DELETE',
  });
}
