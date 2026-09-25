/**
 * frontend/src/components/chat/ChatInput.tsx
 * -------------------------------------------
 * Command Dispatcher (White & Light Blue Style)
 */

import React, { useRef, useState, useCallback, useEffect } from 'react';
import { Square, Trash2, Paperclip, X, AlertTriangle, ArrowRight } from 'lucide-react';
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
    <div className="shrink-0 border-t border-slate-200 bg-white px-6 py-4 font-sans shadow-sm">
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
        <div className="mb-3 flex items-center gap-3 p-3 bg-blue-50/50 border border-blue-100 rounded-lg shadow-sm">
          <div className="relative w-12 h-12 border border-blue-200 rounded overflow-hidden shrink-0">
            <img src={imagePreviewUrl} alt="Attached preview" className="w-full h-full object-cover" />
          </div>
          <div className="flex-1 min-w-0 text-sm">
            <div className="font-semibold text-blue-700 truncate">{attachedImage.name}</div>
            <div className="text-xs text-slate-500 mt-0.5">
              {(attachedImage.size / 1024).toFixed(0)} KB &bull; Image Attached
            </div>
          </div>
          <button
            onClick={handleRemoveImage}
            className="px-3 py-1.5 bg-white text-rose-500 border border-rose-200 font-medium text-xs rounded hover:bg-rose-50 transition-colors"
          >
            Remove
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
          title="Attach image"
          className={`
            flex-none w-11 h-11 border rounded-lg flex items-center justify-center transition-colors
            ${attachedImage
              ? 'bg-blue-500 border-blue-600 text-white shadow-sm'
              : 'bg-slate-50 border-slate-200 text-slate-600 hover:bg-blue-50 hover:text-blue-600 hover:border-blue-200'
            }
            disabled:opacity-50 disabled:cursor-not-allowed
          `}
        >
          <Paperclip className="w-5 h-5" />
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
                ? 'Type a message about the attached image...'
                : 'Type a message...'
            }
            rows={1}
            className="
              w-full resize-none border border-slate-200 rounded-lg
              bg-slate-50 px-4 py-3 text-sm text-slate-800 font-sans
              placeholder:text-slate-400 focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400
              transition-all disabled:opacity-50
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
              className="px-5 h-11 rounded-lg bg-rose-500 hover:bg-rose-600 text-white font-semibold text-sm transition-colors flex items-center gap-2 shadow-sm"
            >
              <Square className="w-4 h-4 fill-current" />
              <span>Stop</span>
            </button>
          ) : (
            <button
              id="send-message-btn"
              onClick={handleSubmit}
              disabled={!canSend}
              className="px-6 h-11 rounded-lg bg-blue-500 hover:bg-blue-600 text-white font-semibold text-sm transition-colors flex items-center gap-2 shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <span>Send</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          )}

          <button
            id="clear-session-btn"
            onClick={onClearSession}
            disabled={isStreaming}
            className="w-11 h-11 rounded-lg border border-slate-200 bg-slate-50 text-slate-500 hover:text-rose-500 hover:border-rose-200 hover:bg-rose-50 transition-colors flex items-center justify-center disabled:opacity-50"
            title="Reset session"
          >
            <Trash2 className="w-5 h-5" />
          </button>
        </div>
      </div>

      <div className="mt-3 flex items-center justify-between text-xs text-slate-400">
        <span>Enter to send, Shift + Enter for new line</span>
        <span className="text-blue-500 font-medium">Memory Active</span>
      </div>
    </div>
  );
};
