import React, { useState } from 'react';
import type { DocumentItem } from '../../types';
import { FileText, Trash2, Database, Layers, Eye } from 'lucide-react';
import { Badge } from '../common/Badge';

interface DocumentListProps {
  documents: DocumentItem[];
  onDelete: (documentId: string) => Promise<void>;
  onSelectDocument?: (documentId: string) => void;
  isLoading?: boolean;
}

export const DocumentList: React.FC<DocumentListProps> = ({
  documents,
  onDelete,
  onSelectDocument,
}) => {
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const handleDelete = async (e: React.MouseEvent, docId: string) => {
    e.stopPropagation();
    if (confirm('Are you sure you want to delete this document from the vector store?')) {
      setDeletingId(docId);
      try {
        await onDelete(docId);
      } finally {
        setDeletingId(null);
      }
    }
  };

  const getFormatBadge = (fileType: string) => {
    const ft = fileType.toLowerCase();
    if (ft === 'pdf') return <Badge variant="rose">PDF</Badge>;
    if (ft === 'docx') return <Badge variant="blue">DOCX</Badge>;
    if (ft === 'md') return <Badge variant="purple">MD</Badge>;
    return <Badge variant="slate">TXT</Badge>;
  };

  if (documents.length === 0) {
    return (
      <div className="p-8 rounded-xl border border-slate-800 bg-[#0d1424]/40 text-center select-none">
        <Database className="w-10 h-10 text-slate-600 mx-auto mb-2" />
        <h3 className="text-sm font-semibold text-slate-300">No Documents Indexed</h3>
        <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">
          Upload documents above to enable semantic retrieval and grounded answers in the Agent Chat.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-800 bg-[#0d1424]/60 overflow-hidden shadow-md">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-slate-800 bg-[#090d16] font-mono text-[11px] text-slate-400">
              <th className="py-3 px-4 font-medium">Document Name</th>
              <th className="py-3 px-4 font-medium">Format</th>
              <th className="py-3 px-4 font-medium">Vector Chunks</th>
              <th className="py-3 px-4 font-medium">Document ID</th>
              <th className="py-3 px-4 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 font-mono text-[11.5px]">
            {documents.map((doc) => {
              const isDeleting = deletingId === doc.document_id;
              return (
                <tr
                  key={doc.document_id}
                  onClick={() => onSelectDocument?.(doc.document_id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      onSelectDocument?.(doc.document_id);
                    }
                  }}
                  tabIndex={0}
                  className="hover:bg-slate-800/60 transition-colors group cursor-pointer focus:outline-none focus:bg-slate-800/80"
                  title="Click anywhere on row to inspect document and vector chunks"
                >
                  {/* Filename */}
                  <td className="py-3.5 px-4 font-sans font-medium text-slate-200">
                    <div className="flex items-center gap-2.5">
                      <div className="w-7 h-7 rounded bg-slate-800 flex items-center justify-center text-slate-300 shrink-0 group-hover:bg-emerald-950/80 group-hover:text-emerald-400 transition-colors border border-slate-700/50">
                        <FileText className="w-4 h-4 text-emerald-400" />
                      </div>
                      <span className="truncate max-w-xs group-hover:text-emerald-300 transition-colors font-medium">
                        {doc.filename}
                      </span>
                    </div>
                  </td>

                  {/* Format */}
                  <td className="py-3.5 px-4">{getFormatBadge(doc.file_type)}</td>

                  {/* Chunks */}
                  <td className="py-3.5 px-4 text-slate-300">
                    <div className="flex items-center gap-1.5 font-mono">
                      <Layers className="w-3.5 h-3.5 text-purple-400" />
                      <span>{doc.chunk_count} chunks</span>
                    </div>
                  </td>

                  {/* ID */}
                  <td className="py-3.5 px-4 text-slate-400 truncate max-w-[140px]">
                    {doc.document_id}
                  </td>

                  {/* Actions */}
                  <td className="py-3.5 px-4 text-right">
                    <div className="inline-flex items-center gap-2">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelectDocument?.(doc.document_id);
                        }}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-mono text-emerald-300 hover:text-white bg-emerald-950/60 hover:bg-emerald-900/80 border border-emerald-700/50 transition-colors shadow-sm"
                        title="Inspect Document & Vector Chunks"
                      >
                        <Eye className="w-3.5 h-3.5 text-emerald-400" />
                        <span>Inspect Chunks</span>
                      </button>

                      <button
                        onClick={(e) => handleDelete(e, doc.document_id)}
                        disabled={isDeleting}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-mono text-rose-400 hover:text-white hover:bg-rose-900/60 border border-rose-800/40 transition-colors disabled:opacity-50"
                        title="Delete from Vector Store"
                      >
                        <Trash2 className="w-3 h-3" />
                        <span>{isDeleting ? 'Deleting...' : 'Delete'}</span>
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

