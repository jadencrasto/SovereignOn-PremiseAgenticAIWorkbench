/**
 * frontend/src/components/chat/ChatInput.tsx
 * -------------------------------------------
 * Command Dispatcher (White & Light Blue Style)
 */

import React, { useRef, useState, useCallback, useEffect } from 'react';
import { Square, Trash2, Paperclip, X, ImageIcon, AlertTriangle, ArrowRight } from 'lucide-react';
import { useWorkbench } from '../../context/WorkbenchContext';
import {
  ALLOWED_IMAGE_TYPES,
  MAX_IMAGE_SIZE_BYTES,
} from '../../types';

interface ChatInputProps {
  onSendMessage: (text: string, image?: File) => void;
  onStopStream: () => void;
  isStreaming: boolean;
  onClearSession: () => void;
}

export const ChatInput: React.FC<ChatInputProps> = ({
  onSendMessage,
  onStopStream,
  isStreaming,
  onClearSession,
}) => {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { selectedModel } = useWorkbench();

  const [attachedImage, setAttachedImage] = useState<File | null>(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);

  useEffect(() => {
    return () => {
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    };
  }, [imagePreviewUrl]);

  const handleSubmit = useCallback(() => {
    if (!value.trim() || isStreaming) return;
    onSendMessage(value.trim(), attachedImage ?? undefined);
    setValue('');
    setAttachedImage(null);
    if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    setImagePreviewUrl(null);
    setImageError(null);
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [value, isStreaming, onSendMessage, attachedImage, imagePreviewUrl]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value);
    const el = textareaRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
    }
  };

  const handleAttachClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    e.target.value = '';

    if (!ALLOWED_IMAGE_TYPES.includes(file.type as any)) {
      setImageError(`INVALID FORMAT: Allowed PNG, JPEG, WebP.`);
      return;
    }
    if (file.size > MAX_IMAGE_SIZE_BYTES) {
      const mb = (file.size / (1024 * 1024)).toFixed(1);
      setImageError(`OVERSIZE ERROR: (${mb} MB exceeds 10 MB limit).`);
      return;
    }

    setImageError(null);
    if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);

    const url = URL.createObjectURL(file);
    setAttachedImage(file);
    setImagePreviewUrl(url);
  };

  const handleRemoveImage = () => {
    setAttachedImage(null);
    if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    setImagePreviewUrl(null);
    setImageError(null);
  };

  const canSend = !isStreaming && value.trim().length > 0;

  return (
    <div className="shrink-0 border-t-2 border-[#cbd5e1] bg-white px-6 py-4 font-mono shadow-sm">
      {imageError && (
        <div className="mb-2.5 flex items-center justify-between p-2.5 bg-[#ffe4e6] text-[#be123c] font-bold text-xs border-2 border-[#f43f5e]">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            <span>{imageError}</span>
          </div>
          <button onClick={() => setImageError(null)}>
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {attachedImage && imagePreviewUrl && (
        <div className="mb-3 flex items-center gap-3 p-2.5 bg-[#f0f9ff] border-2 border-[#0284c7] brutal-shadow-blue">
          <div className="relative w-12 h-12 border border-[#0284c7] shrink-0">
            <img src={imagePreviewUrl} alt="Attached preview" className="w-full h-full object-cover" />
          </div>
          <div className="flex-1 min-w-0 text-xs">
            <div className="font-bold text-[#0284c7] truncate uppercase">{attachedImage.name}</div>
            <div className="text-[10px] text-slate-500">
              {(attachedImage.size / 1024).toFixed(0)} KB &bull; VISION INFERENCE ARMED
            </div>
          </div>
          <button
            onClick={handleRemoveImage}
            className="px-2 py-1 bg-white text-[#e11d48] border border-[#e11d48] font-bold text-[10px] uppercase hover:bg-[#ffe4e6]"
          >
            REMOVE
          </button>
        </div>
      )}

      {/* Input row */}
      <div className="flex items-end gap-3">
        <input
          ref={fileInputRef}
          type="file"
          accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"
          className="hidden"
          onChange={handleFileSelect}
          id="image-file-input"
        />

        {/* Attach button */}
        <button
          onClick={handleAttachClick}
          disabled={isStreaming}
          title="Attach inspection photograph"
          className={`
            flex-none w-11 h-11 border-2 flex items-center justify-center font-bold text-xs brutal-btn
            ${attachedImage
              ? 'bg-[#0284c7] border-black text-white brutal-shadow-dark'
              : 'bg-[#f8fafc] border-[#cbd5e1] text-slate-700 hover:border-[#0284c7] hover:text-[#0284c7]'
            }
            disabled:opacity-30 disabled:cursor-not-allowed
          `}
        >
          <Paperclip className="w-4 h-4" />
        </button>

        {/* Text Area */}
        <div className="flex-1 relative">
          <textarea
            ref={textareaRef}
            id="chat-input-textarea"
            value={value}
            onChange={handleTextChange}
            onKeyDown={handleKeyDown}
            disabled={isStreaming}
            placeholder={
              attachedImage
                ? 'ENTER INSPECTION DIRECTIVE FOR ATTACHED PHOTOGRAPH...'
                : 'DISPATCH COMMAND OR QUERY REFINERY SPECIFICATIONS...'
            }
            rows={1}
            className="
              w-full resize-none border-2 border-[#cbd5e1]
              bg-[#f8fafc] px-4 py-3 text-xs text-[#0f172a] font-mono font-medium
              placeholder:text-slate-500 focus:outline-none focus:border-[#0284c7]
              transition-all disabled:opacity-40
              min-h-[44px] max-h-[160px] leading-relaxed
            "
            style={{ height: 'auto' }}
          />
        </div>

        {/* Execute / Clear */}
        <div className="flex items-center gap-2">
          {isStreaming ? (
            <button
              id="stop-stream-btn"
              onClick={onStopStream}
              className="px-5 h-11 bg-[#e11d48] text-white font-black text-xs uppercase border-2 border-black brutal-shadow-dark flex items-center gap-2 brutal-btn"
            >
              <Square className="w-4 h-4 fill-current" />
              <span>ABORT</span>
            </button>
          ) : (
            <button
              id="send-message-btn"
              onClick={handleSubmit}
              disabled={!canSend}
              className="px-6 h-11 bg-[#0284c7] hover:bg-[#0369a1] text-white font-black text-xs uppercase border-2 border-black brutal-shadow-dark flex items-center gap-2 brutal-btn disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <span>EXECUTE</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          )}

          <button
            id="clear-session-btn"
            onClick={onClearSession}
            disabled={isStreaming}
            className="w-11 h-11 border-2 border-[#cbd5e1] bg-[#f8fafc] text-slate-600 hover:text-[#e11d48] hover:border-[#e11d48] flex items-center justify-center brutal-btn disabled:opacity-30"
            title="Reset session"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="mt-2.5 flex items-center justify-between text-[10px] font-bold text-slate-500 uppercase">
        <span>[ENTER] DISPATCH &bull; [SHIFT+ENTER] NEWLINE</span>
        <span className="text-[#059669]">HOST_MEMORY: DETERMINISTIC_EVICTION_ACTIVE</span>
      </div>
    </div>
  );
};
