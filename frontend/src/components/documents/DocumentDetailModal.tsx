/**
 * frontend/src/components/documents/DocumentDetailModal.tsx
 * -----------------------------------------------------------
 * Read-only modal inspector for indexed RAG documents and vector chunks.
 *
 * Displays:
 * - Canonical document metadata (document_id, filename, file_type, chunk_count)
 * - Actual stored chunk text and chunk metadata from ChromaDB
 * - Copy chunk content functionality
 */

import React, { useEffect, useState } from 'react';
import type { DocumentDetailResponse } from '../../types';
import { fetchDocumentDetails } from '../../api/documents';
import { FileText, Layers, Copy, Check, X, Database, Shield } from 'lucide-react';
import { Badge } from '../common/Badge';

interface DocumentDetailModalProps {
  documentId: string | null;
  onClose: () => void;
}

export const DocumentDetailModal: React.FC<DocumentDetailModalProps> = ({
  documentId,
  onClose,
}) => {
  const [doc, setDoc] = useState<DocumentDetailResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [copiedChunkId, setCopiedChunkId] = useState<string | null>(null);

  useEffect(() => {
    if (!documentId) {
      setDoc(null);
      return;
    }

    let isMounted = true;
    setLoading(true);
    setError(null);

    fetchDocumentDetails(documentId)
      .then((data) => {
        if (isMounted) {
          setDoc(data);
        }
      })
      .catch((err: any) => {
        if (isMounted) {
          setError(err.message || 'Failed to retrieve document details');
        }
      })
      .finally(() => {
        if (isMounted) {
          setLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [documentId]);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    if (documentId) {
      window.addEventListener('keydown', handleKeyDown);
    }
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [documentId, onClose]);

  if (!documentId) return null;

  const copyToClipboard = (text: string, chunkId: string) => {
    navigator.clipboard.writeText(text);
    setCopiedChunkId(chunkId);
    setTimeout(() => {
      setCopiedChunkId(null);
    }, 2000);
  };

  const getFormatBadge = (fileType: string) => {
    const ft = (fileType || '').toLowerCase();
    if (ft === 'pdf') return <Badge variant="rose">PDF</Badge>;
    if (ft === 'docx') return <Badge variant="blue">DOCX</Badge>;
    if (ft === 'md') return <Badge variant="purple">MD</Badge>;
    return <Badge variant="slate">TXT</Badge>;
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4 overflow-y-auto"
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          onClose();
        }
      }}
    >
      <div className="w-full max-w-4xl bg-white border border-slate-200 rounded-2xl shadow-xl overflow-hidden my-8 flex flex-col max-h-[90vh]">
        {/* Modal Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 bg-slate-50">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-blue-100 flex items-center justify-center text-blue-600">
              <FileText className="w-4 h-4" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-bold text-slate-900 tracking-tight">
                  {doc ? doc.filename : 'Document Inspection'}
                </h2>
                {doc && getFormatBadge(doc.file_type)}
              </div>
              <p className="text-xs font-mono text-slate-500 mt-0.5">
                ID: {documentId}
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition"
            title="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Content */}
        <div className="p-6 overflow-y-auto flex-1 space-y-6">
          {loading ? (
            <div className="py-16 text-center text-slate-500 text-sm flex flex-col items-center justify-center gap-2">
              <Layers className="w-6 h-6 animate-pulse text-emerald-600" />
              <span>Retrieving indexed document metadata and vector chunks...</span>
            </div>
          ) : error ? (
            <div className="p-4 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm shadow-sm">
              <p className="font-bold">Error Loading Document</p>
              <p className="text-xs mt-1 text-red-600">{error}</p>
            </div>
          ) : doc ? (
            <>
              {/* Metadata Cards */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="p-3.5 rounded-xl border border-slate-200 bg-slate-50 space-y-1 shadow-sm">
                  <div className="flex items-center justify-between text-xs text-slate-500 font-mono">
                    <span>Vector Chunks</span>
                    <Layers className="w-3.5 h-3.5 text-purple-500" />
                  </div>
                  <div className="text-lg font-bold text-slate-900 font-mono">{doc.chunk_count}</div>
                  <div className="text-[11px] text-slate-500 font-mono">Indexed in ChromaDB</div>
                </div>

                <div className="p-3.5 rounded-xl border border-slate-200 bg-slate-50 space-y-1 shadow-sm">
                  <div className="flex items-center justify-between text-xs text-slate-500 font-mono">
                    <span>File Format</span>
                    <Database className="w-3.5 h-3.5 text-emerald-500" />
                  </div>
                  <div className="text-lg font-bold text-slate-900 font-mono uppercase">{doc.file_type || 'TXT'}</div>
                  <div className="text-[11px] text-slate-500 font-mono">Preserved in data/uploads</div>
                </div>

                <div className="p-3.5 rounded-xl border border-slate-200 bg-slate-50 space-y-1 shadow-sm">
                  <div className="flex items-center justify-between text-xs text-slate-500 font-mono">
                    <span>Air-Gap Storage</span>
                    <Shield className="w-3.5 h-3.5 text-blue-500" />
                  </div>
                  <div className="text-xs font-semibold text-emerald-600 font-mono mt-1.5">100% Local Vector Index</div>
                  <div className="text-[11px] text-slate-500 font-mono">Read-Only Grounding</div>
                </div>
              </div>

              {/* Chunks List */}
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500 font-mono flex items-center gap-2">
                    <Layers className="w-3.5 h-3.5 text-purple-600" />
                    Vector Store Chunks & Text Grounding ({doc.chunks.length})
                  </h3>
                  <span className="text-[11px] font-mono text-slate-500">
                    Model: nomic-embed-text
                  </span>
                </div>

                {doc.chunks.length === 0 ? (
                  <div className="p-4 rounded-xl border border-slate-200 bg-slate-50 text-center text-xs text-slate-500 shadow-sm">
                    No chunk content available for this document.
                  </div>
                ) : (
                  <div className="space-y-3">
                    {doc.chunks.map((chunk, idx) => (
                      <div
                        key={chunk.chunk_id || idx}
                        className="rounded-xl border border-slate-200 bg-white overflow-hidden transition hover:border-slate-300 shadow-sm"
                      >
                        {/* Chunk Header */}
                        <div className="flex items-center justify-between px-4 py-2.5 bg-slate-100 border-b border-slate-200 text-xs">
                          <div className="flex items-center gap-2">
                            <span className="px-2 py-0.5 rounded bg-purple-100 border border-purple-200 text-purple-700 font-mono text-[11px] font-bold shadow-sm">
                              Chunk {chunk.chunk_index + 1}
                            </span>
                            <span className="font-mono text-slate-500 text-[11px] truncate max-w-xs">
                              {chunk.chunk_id}
                            </span>
                            {chunk.page && (
                              <span className="px-1.5 py-0.5 rounded bg-white border border-slate-200 shadow-sm text-slate-600 font-mono text-[10px]">
                                Page {chunk.page}
                              </span>
                            )}
                          </div>

                          <div className="flex items-center gap-2">
                            <span className="text-[11px] font-mono text-slate-500">
                              {chunk.text.length} chars
                            </span>
                            <button
                              onClick={() => copyToClipboard(chunk.text, chunk.chunk_id)}
                              className="p-1 text-slate-500 hover:text-slate-900 rounded hover:bg-white transition flex items-center gap-1 text-[11px] shadow-sm"
                              title="Copy chunk text"
                            >
                              {copiedChunkId === chunk.chunk_id ? (
                                <>
                                  <Check className="w-3.5 h-3.5 text-emerald-600" />
                                  <span className="text-emerald-600 font-mono font-semibold">Copied</span>
                                </>
                              ) : (
                                <>
                                  <Copy className="w-3.5 h-3.5" />
                                  <span className="font-mono font-semibold">Copy</span>
                                </>
                              )}
                            </button>
                          </div>
                        </div>

                        {/* Chunk Text Content */}
                        <div className="p-4">
                          <pre className="text-xs text-slate-700 font-mono whitespace-pre-wrap leading-relaxed select-text overflow-x-auto max-h-64 overflow-y-auto bg-slate-50 p-3 rounded-lg border border-slate-200">
                            {chunk.text}
                          </pre>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          ) : null}
        </div>

        {/* Modal Footer */}
        <div className="px-6 py-3 border-t border-slate-200 bg-slate-50 flex items-center justify-between text-xs text-slate-500 font-mono">
          <span>Read-only local inspection — no data mutated</span>
          <button
            onClick={onClose}
            className="px-4 py-1.5 bg-slate-200 hover:bg-slate-300 text-slate-700 rounded-lg text-xs font-semibold transition"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
