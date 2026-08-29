import React, { useRef, useState, useCallback, useEffect } from 'react';
import { Send, Square, Trash2, Paperclip, X, ImageIcon, AlertTriangle } from 'lucide-react';
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

  // Phase 5: image attachment state
  const [attachedImage, setAttachedImage] = useState<File | null>(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);

  // Revoke object URL on unmount or when image changes
  useEffect(() => {
    return () => {
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    };
  }, [imagePreviewUrl]);

  const handleSubmit = useCallback(() => {
    if (!value.trim() || isStreaming) return;
    onSendMessage(value.trim(), attachedImage ?? undefined);
    setValue('');
    // Clear image after send
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
    // Auto-resize textarea
    const el = textareaRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
    }
  };

  // Phase 5: image attachment handlers
  const handleAttachClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // Reset input so same file can be re-selected after removal
    e.target.value = '';

    // Client-side validation
    if (!ALLOWED_IMAGE_TYPES.includes(file.type as any)) {
      setImageError(`Unsupported format. Allowed: PNG, JPEG, WebP.`);
      return;
    }
    if (file.size > MAX_IMAGE_SIZE_BYTES) {
      const mb = (file.size / (1024 * 1024)).toFixed(1);
      setImageError(`Image too large (${mb} MB). Maximum: 10 MB.`);
      return;
    }

    setImageError(null);

    // Revoke previous preview URL
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

  const isVisionModel = selectedModel?.includes('llava');
  const canSend = !isStreaming && value.trim().length > 0;

  return (
    <div className="shrink-0 border-t border-slate-800/80 bg-[#0c121e]/95 backdrop-blur-sm px-4 py-3">
      {/* Image error banner */}
      {imageError && (
        <div className="mb-2 flex items-center gap-2 px-3 py-1.5 rounded-lg bg-rose-950/60 border border-rose-800/60 text-xs text-rose-300">
          <AlertTriangle className="w-3.5 h-3.5 text-rose-400 shrink-0" />
          <span>{imageError}</span>
          <button
            onClick={() => setImageError(null)}
            className="ml-auto text-rose-500 hover:text-rose-300 transition-colors"
          >
            <X className="w-3 h-3" />
          </button>
        </div>
      )}

      {/* Image preview strip */}
      {attachedImage && imagePreviewUrl && (
        <div className="mb-2 flex items-center gap-3 p-2 rounded-lg bg-blue-950/30 border border-blue-700/30">
          <div className="relative w-12 h-12 rounded-md overflow-hidden border border-blue-600/40 shrink-0">
            <img
              src={imagePreviewUrl}
              alt="Attached image preview"
              className="w-full h-full object-cover"
            />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 text-xs text-blue-300 font-mono">
              <ImageIcon className="w-3 h-3 text-blue-400" />
              <span className="truncate">{attachedImage.name}</span>
            </div>
            <div className="text-[11px] text-slate-500 mt-0.5">
              {(attachedImage.size / 1024).toFixed(0)} KB · {attachedImage.type}
            </div>
            {!isVisionModel && (
              <div className="text-[11px] text-amber-500/80 mt-0.5">
                Vision mode will auto-activate (llava:7b)
              </div>
            )}
          </div>
          <button
            onClick={handleRemoveImage}
            className="w-6 h-6 flex items-center justify-center rounded-full text-slate-500 hover:text-rose-400 hover:bg-rose-950/50 transition-colors shrink-0"
            title="Remove image"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Input row */}
      <div className="flex items-end gap-2">
        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"
          className="hidden"
          onChange={handleFileSelect}
          id="image-file-input"
        />

        {/* Attach image button */}
        <button
          onClick={handleAttachClick}
          disabled={isStreaming}
          title="Attach image (PNG, JPEG, WebP · max 10 MB)"
          id="attach-image-btn"
          className={`
            flex-none mb-0.5 w-8 h-8 rounded-lg flex items-center justify-center
            transition-all duration-150 border
            ${attachedImage
              ? 'bg-blue-600/20 border-blue-500/50 text-blue-400'
              : 'bg-slate-800/60 border-slate-700/50 text-slate-400 hover:text-blue-400 hover:border-blue-600/40 hover:bg-blue-950/30'
            }
            disabled:opacity-40 disabled:cursor-not-allowed
          `}
        >
          <Paperclip className="w-3.5 h-3.5" />
        </button>

        {/* Textarea */}
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
                ? 'Ask a question about this image...'
                : 'Message the Sovereign Assistant (Shift+Enter for new line)'
            }
            rows={1}
            className="
              w-full resize-none rounded-xl border border-slate-700/60
              bg-slate-900/80 px-4 py-2.5 pr-12 text-sm text-slate-100
              placeholder:text-slate-500 focus:outline-none focus:border-blue-500/60
              focus:ring-1 focus:ring-blue-500/20 transition-all
              disabled:opacity-50 disabled:cursor-not-allowed
              min-h-[40px] max-h-[200px] leading-relaxed
            "
            style={{ height: 'auto' }}
          />
          {/* Character hint */}
          {value.length > 20000 && (
            <span className="absolute right-3 bottom-2.5 text-[10px] text-amber-500 font-mono">
              {value.length}/32000
            </span>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex flex-col gap-1.5 mb-0.5">
          {/* Send / Stop button */}
          {isStreaming ? (
            <button
              id="stop-stream-btn"
              onClick={onStopStream}
              className="
                flex-none w-8 h-8 rounded-lg border border-rose-700/60
                bg-rose-950/60 text-rose-400 flex items-center justify-center
                hover:bg-rose-900/80 hover:text-rose-300 transition-all duration-150
              "
              title="Stop generation"
            >
              <Square className="w-3.5 h-3.5" />
            </button>
          ) : (
            <button
              id="send-message-btn"
              onClick={handleSubmit}
              disabled={!canSend}
              className="
                flex-none w-8 h-8 rounded-lg border border-blue-600/50
                bg-blue-600/20 text-blue-400 flex items-center justify-center
                hover:bg-blue-600/30 hover:text-blue-300 transition-all duration-150
                disabled:opacity-30 disabled:cursor-not-allowed
              "
              title="Send message (Enter)"
            >
              <Send className="w-3.5 h-3.5" />
            </button>
          )}

          {/* Clear session */}
          <button
            id="clear-session-btn"
            onClick={onClearSession}
            disabled={isStreaming}
            className="
              flex-none w-8 h-8 rounded-lg border border-slate-700/40
              bg-slate-900/60 text-slate-500 flex items-center justify-center
              hover:text-rose-400 hover:border-rose-700/40 hover:bg-rose-950/30
              transition-all duration-150 disabled:opacity-30 disabled:cursor-not-allowed
            "
            title="Clear conversation"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Footer hint */}
      <div className="mt-1.5 flex items-center justify-between px-1">
        <span className="text-[10px] text-slate-600 font-mono">
          {attachedImage
            ? '📎 Vision pipeline: llava:7b → qwen2.5:7b'
            : 'Enter to send · Shift+Enter for new line'
          }
        </span>
        <span className="text-[10px] text-slate-700 font-mono">
          Sovereign Local Mode
        </span>
      </div>
    </div>
  );
};
