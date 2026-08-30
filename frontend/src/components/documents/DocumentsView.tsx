import React, { useState } from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import { DropZone } from './DropZone';
import { DocumentList } from './DocumentList';
import { DocumentDetailModal } from './DocumentDetailModal';
import { uploadDocument, deleteDocument } from '../../api/documents';
import { FileText, RefreshCw, Database, Shield, Layers } from 'lucide-react';

export const DocumentsView: React.FC = () => {
  const { documents, isDocsLoading, refreshDocuments, addToast } = useWorkbench();
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);

  const handleUpload = async (file: File) => {
    setIsUploading(true);
    try {
      const res = await uploadDocument(file);
      addToast('success', `Indexed "${res.filename}" into ${res.chunks} vector chunks.`);
      await refreshDocuments();
    } catch (err: any) {
      addToast('error', err.message || 'Failed to ingest document.');
    } finally {
      setIsUploading(false);
    }
  };

  const handleDelete = async (documentId: string) => {
    try {
      const res = await deleteDocument(documentId);
      addToast('success', `Deleted document (${res.chunks_deleted} chunks purged).`);
      if (selectedDocId === documentId) {
        setSelectedDocId(null);
      }
      await refreshDocuments();
    } catch (err: any) {
      addToast('error', err.message || 'Failed to delete document.');
    }
  };

  const totalChunks = documents.reduce((acc, d) => acc + d.chunk_count, 0);

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto bg-[#090d16] p-6">
      <div className="max-w-5xl mx-auto w-full space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
              <FileText className="w-5 h-5 text-emerald-400" />
              Document Knowledge & Local RAG
            </h1>
            <p className="text-xs text-slate-400 mt-1 max-w-xl">
              Ingest enterprise documents into the local ChromaDB vector store. Text is chunked with metadata preservation and embedded using Ollama's <code className="text-emerald-400">nomic-embed-text</code>. Click any document to inspect stored chunks.
            </p>
          </div>

          <button
            onClick={() => refreshDocuments()}
            disabled={isDocsLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-900 hover:bg-slate-800 text-xs font-mono text-slate-300 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isDocsLoading ? 'animate-spin text-emerald-400' : ''}`} />
            <span>Refresh</span>
          </button>
        </div>

        {/* Stats Row */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="p-4 rounded-xl border border-slate-800 bg-[#0d1424]/60 space-y-1">
            <div className="flex items-center justify-between text-xs text-slate-400 font-mono">
              <span>Indexed Documents</span>
              <Database className="w-4 h-4 text-emerald-400" />
            </div>
            <div className="text-2xl font-bold text-white font-mono">{documents.length}</div>
            <div className="text-[11px] text-slate-400 font-mono">Storage: data/chromadb</div>
          </div>

          <div className="p-4 rounded-xl border border-slate-800 bg-[#0d1424]/60 space-y-1">
            <div className="flex items-center justify-between text-xs text-slate-400 font-mono">
              <span>Total Vector Chunks</span>
              <Layers className="w-4 h-4 text-purple-400" />
            </div>
            <div className="text-2xl font-bold text-white font-mono">{totalChunks}</div>
            <div className="text-[11px] text-slate-400 font-mono">Model: nomic-embed-text</div>
          </div>

          <div className="p-4 rounded-xl border border-slate-800 bg-[#0d1424]/60 space-y-1">
            <div className="flex items-center justify-between text-xs text-slate-400 font-mono">
              <span>Supported Ingestion</span>
              <Shield className="w-4 h-4 text-blue-400" />
            </div>
            <div className="text-sm font-semibold text-emerald-400 font-mono mt-1">PDF, DOCX, TXT, MD</div>
            <div className="text-[11px] text-slate-400 font-mono">100% On-Premise Air-Gapped</div>
          </div>
        </div>

        {/* Drag & Drop Upload */}
        <div>
          <h2 className="text-sm font-semibold text-slate-200 mb-2">Upload New Document</h2>
          <DropZone onUpload={handleUpload} isUploading={isUploading} />
        </div>

        {/* Document Table */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-slate-200">Indexed Knowledge Base</h2>
            <span className="text-xs font-mono text-slate-500">{documents.length} files (click row to inspect)</span>
          </div>
          <DocumentList
            documents={documents}
            onDelete={handleDelete}
            onSelectDocument={setSelectedDocId}
            isLoading={isDocsLoading}
          />
        </div>
      </div>

      {/* Document Detail / Chunk Inspector Modal */}
      <DocumentDetailModal
        documentId={selectedDocId}
        onClose={() => setSelectedDocId(null)}
      />
    </div>
  );
};

