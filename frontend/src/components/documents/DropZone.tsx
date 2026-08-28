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
            ? 'border-emerald-500 bg-emerald-950/20 text-emerald-300'
            : isUploading
            ? 'border-slate-700 bg-slate-900/40 opacity-75 cursor-wait'
            : 'border-slate-800 bg-[#0d1424]/50 hover:border-slate-700 hover:bg-[#0d1424]'
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
              isDragOver ? 'bg-emerald-900/60 text-emerald-400 scale-110' : 'bg-slate-800 text-slate-300'
            }`}
          >
            {isUploading ? (
              <Loader2 className="w-6 h-6 animate-spin text-emerald-400" />
            ) : (
              <UploadCloud className="w-6 h-6" />
            )}
          </div>

          <div>
            <p className="text-sm font-semibold text-slate-200">
              {isUploading ? 'Ingesting, Chunking & Embedding...' : 'Drop documents here to index'}
            </p>
            <p className="text-xs text-slate-400 mt-0.5">
              or click to browse from local filesystem
            </p>
          </div>

          <div className="flex items-center gap-2 mt-1">
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
              PDF · DOCX · TXT · MD
            </span>
            <span className="text-[10px] text-slate-500 font-mono">Max 50MB</span>
          </div>
        </div>
      </div>

      {clientError && (
        <div className="flex items-center gap-2 p-2.5 rounded-lg bg-rose-950/60 border border-rose-800/50 text-rose-300 text-xs font-mono animate-in fade-in">
          <AlertCircle className="w-4 h-4 shrink-0 text-rose-400" />
          <span>{clientError}</span>
        </div>
      )}
    </div>
  );
};
