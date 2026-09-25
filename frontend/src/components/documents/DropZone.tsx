import React, { useState, useRef } from 'react';
import { UploadCloud, AlertCircle, Loader2 } from 'lucide-react';

interface DropZoneProps {
  onUpload: (file: File) => Promise<void>;
  isUploading: boolean;
}

const SUPPORTED_EXTENSIONS = ['.pdf', '.txt', '.md', '.docx'];

export const DropZone: React.FC<DropZoneProps> = ({ onUpload, isUploading }) => {
  const [isDragOver, setIsDragOver] = useState(false);
  const [clientError, setClientError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const validateAndUpload = async (file: File) => {
    setClientError(null);
    const ext = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!SUPPORTED_EXTENSIONS.includes(ext)) {
      setClientError(`Unsupported file format "${ext}". Supported: PDF, DOCX, TXT, MD`);
      return;
    }

    if (file.size > 50 * 1024 * 1024) {
      setClientError(`File is too large (${(file.size / 1024 / 1024).toFixed(1)}MB). Max 50MB.`);
      return;
    }

    await onUpload(file);
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(false);
    if (isUploading) return;

    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      validateAndUpload(e.dataTransfer.files[0]);
    }
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    if (!isUploading) setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      validateAndUpload(e.target.files[0]);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  return (
    <div className="space-y-2">
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => !isUploading && fileInputRef.current?.click()}
        className={`p-6 rounded-xl border-2 border-dashed transition-all cursor-pointer text-center select-none ${
          isDragOver
            ? 'border-blue-500 bg-blue-50 text-blue-700'
            : isUploading
            ? 'border-slate-300 bg-slate-100 opacity-75 cursor-wait'
            : 'border-slate-300 bg-slate-50 hover:border-blue-400 hover:bg-blue-50/50'
        }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.txt,.md,.docx"
          onChange={handleFileChange}
          className="hidden"
          disabled={isUploading}
        />

        <div className="flex flex-col items-center justify-center gap-2 max-w-sm mx-auto">
          <div
            className={`w-12 h-12 rounded-xl flex items-center justify-center transition-transform ${
              isDragOver ? 'bg-blue-100 text-blue-600 scale-110' : 'bg-white border border-slate-200 text-slate-400 shadow-sm'
            }`}
          >
            {isUploading ? (
              <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
            ) : (
              <UploadCloud className="w-6 h-6" />
            )}
          </div>

          <div>
            <p className="text-sm font-semibold text-slate-700">
              {isUploading ? 'Ingesting, Chunking & Embedding...' : 'Drop documents here to index'}
            </p>
            <p className="text-xs text-slate-500 mt-0.5">
              or click to browse from local filesystem
            </p>
          </div>

          <div className="flex items-center gap-2 mt-1">
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-100 text-slate-600 border border-slate-200">
              PDF · DOCX · TXT · MD
            </span>
            <span className="text-[10px] text-slate-500 font-mono">Max 50MB</span>
          </div>
        </div>
      </div>

      {clientError && (
        <div className="flex items-center gap-2 p-2.5 rounded-lg bg-red-50 border border-red-200 text-red-600 text-xs font-mono animate-in fade-in">
          <AlertCircle className="w-4 h-4 shrink-0 text-red-500" />
          <span>{clientError}</span>
        </div>
      )}
    </div>
  );
};
