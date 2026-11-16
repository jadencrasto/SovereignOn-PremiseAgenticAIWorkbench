/**
 * frontend/src/components/chat/ChatInput.tsx
 * -------------------------------------------
 * Command Dispatcher (White & Light Blue Style)
 */

import React, { useRef, useState, useCallback, useEffect } from 'react';
import { Square, Trash2, Paperclip, X, AlertTriangle, ArrowRight } from 'lucide-react';
import { previewCsvFile, previewTextFile, type CsvPreview, type TextFileLineRange, type TextFilePreview } from '../../api/chat';
import {
  ALLOWED_IMAGE_TYPES,
  MAX_IMAGE_SIZE_BYTES,
} from '../../types';

interface ChatInputProps {
  onSendMessage: (text: string, image?: File) => void;
  onStopStream: () => void;
  isStreaming: boolean;
  sendBlocked: boolean;
  imageBlocked: boolean;
  hasEarlierImage?: boolean;
  hasEarlierTextFile?: boolean;
  hasEarlierCsvFile?: boolean;
  sessionId?: string;
  onSendTextFile?: (question: string, file: File, range?: TextFileLineRange) => void;
  sendStatus?: string;
  imageStatus?: string;
  onClearSession: () => void;
}

export const ChatInput: React.FC<ChatInputProps> = ({
  onSendMessage,
  onStopStream,
  isStreaming,
  sendBlocked,
  imageBlocked,
  hasEarlierImage,
  hasEarlierTextFile,
  hasEarlierCsvFile,
  sessionId,
  onSendTextFile,
  sendStatus,
  imageStatus,
  onClearSession,
}) => {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textInputRef = useRef<HTMLInputElement>(null);
  const textUpload = useRef<AbortController | null>(null);

  const [attachedImage, setAttachedImage] = useState<File | null>(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [textFile, setTextFile] = useState<{
    file: File;
    state: 'loading' | 'ready' | 'failed';
    preview?: TextFilePreview | CsvPreview;
    range?: TextFileLineRange;
  } | null>(null);
  const [firstLine, setFirstLine] = useState('');
  const [lastLine, setLastLine] = useState('');
  const csvFile = textFile?.file.name.toLowerCase().endsWith('.csv') ?? false;
  const preview = textFile?.preview;
  const csvPreview = preview && 'headers' in preview ? preview : undefined;
  const lineCount = csvPreview?.row_count ?? (preview && 'line_count' in preview ? preview.line_count : 0);
  const wantsRange = Boolean(firstLine || lastLine);
  const validRange = [firstLine, lastLine].every(bound => /^[1-9][0-9]{0,3}$/.test(bound)) &&
    Number(firstLine) <= Number(lastLine) && Number(lastLine) <= lineCount &&
    (!csvFile || Number(lastLine) - Number(firstLine) < 12);
  const previewedRange = textFile?.range;
  const rangeMatches = wantsRange
    ? validRange && previewedRange?.start === Number(firstLine) && previewedRange.end === Number(lastLine)
    : !csvFile && !previewedRange;
  const questionTooLong = Boolean(textFile && new TextEncoder().encode(value.trim()).length > 1024);
  const textFileBlocked = Boolean(textFile && (!onSendTextFile || textFile.state !== 'ready' || !rangeMatches || questionTooLong));

  useEffect(() => () => {
    textUpload.current?.abort();
    textUpload.current = null;
  }, []);

  const removeTextFile = useCallback(() => {
    textUpload.current?.abort();
    textUpload.current = null;
    setTextFile(null);
    setFirstLine('');
    setLastLine('');
    setImageError(null);
  }, []);

  useEffect(() => {
    return () => {
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    };
  }, [imagePreviewUrl]);

  const handleSubmit = useCallback(() => {
    if (!value.trim() || isStreaming || textUpload.current || textFileBlocked ||
        (attachedImage ? imageBlocked : sendBlocked)) return;
    if (textFile) {
      if (!onSendTextFile) return;
      if (textFile.range) onSendTextFile(value.trim(), textFile.file, textFile.range);
      else onSendTextFile(value.trim(), textFile.file);
      removeTextFile();
    } else {
      onSendMessage(value.trim(), attachedImage ?? undefined);
    }
    setValue('');
    setAttachedImage(null);
    if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    setImagePreviewUrl(null);
    setImageError(null);
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [value, isStreaming, onSendMessage, attachedImage, imagePreviewUrl, sendBlocked, imageBlocked,
    textFile, textFileBlocked, onSendTextFile, removeTextFile]);

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
    if (!imageBlocked) fileInputRef.current?.click();
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || imageBlocked || isStreaming) return;

    e.target.value = '';
    removeTextFile();

    if (!ALLOWED_IMAGE_TYPES.includes(file.type as any)) {
      setAttachedImage(null);
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
      setImagePreviewUrl(null);
      setImageError(`INVALID FORMAT: Allowed PNG, JPEG, WebP.`);
      return;
    }
    if (file.size === 0 || file.size > MAX_IMAGE_SIZE_BYTES) {
      setAttachedImage(null);
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
      setImagePreviewUrl(null);
      const mb = (file.size / (1024 * 1024)).toFixed(1);
      setImageError(file.size === 0 ? 'Image file is empty.' : `OVERSIZE ERROR: (${mb} MB exceeds 10 MB limit).`);
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

  const loadTextPreview = async (file: File, range?: TextFileLineRange, previous?: NonNullable<typeof textFile>) => {
    const upload = new AbortController();
    textUpload.current = upload;
    setImageError(null);
    setTextFile({ ...previous, file, state: 'loading' });
    try {
      const readPreview = file.name.toLowerCase().endsWith('.csv') ? previewCsvFile : previewTextFile;
      const preview = await readPreview(file, sessionId, upload.signal, range);
      if (textUpload.current === upload) setTextFile({ file, state: 'ready', preview, range });
    } catch (error) {
      if (textUpload.current !== upload) return;
      setTextFile({ ...previous, file, state: 'failed' });
      const status = typeof error === 'object' && error && 'status' in error ? error.status : undefined;
      setImageError(file.name.toLowerCase().endsWith('.csv')
        ? 'CSV preview failed. Use a UTF-8 comma-delimited file with unique headers, up to 2 KiB and 32 rows.'
        : status === 413 ? 'The text file exceeds the preview limit.' :
        status === 422 && range ? 'Choose a line range containing text, or reselect the file.' :
          status === 422 ? 'The file must contain valid UTF-8 text without unsafe control characters.' :
          'Text preview is unavailable. Remove the file or try selecting it again.');
    } finally {
      if (textUpload.current === upload) textUpload.current = null;
    }
  };

  const handleTextFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || isStreaming) return;
    event.target.value = '';
    removeTextFile();
    handleRemoveImage();
    const name = file.name.toLowerCase();
    const media = ['', 'text/plain', 'application/octet-stream'];
    if (name.endsWith('.csv')) media.push('text/csv', 'application/vnd.ms-excel');
    if ((!name.endsWith('.txt') && !name.endsWith('.csv')) || !media.includes(file.type)) {
      setImageError('Choose a UTF-8 .txt or .csv file.');
      return;
    }
    if (!file.size || file.size > 2048) {
      setImageError(file.size ? 'The text file exceeds 2 KiB.' : 'The text file is empty.');
      return;
    }
    return loadTextPreview(file);
  };

  const canSend = !isStreaming && !textFileBlocked && !(attachedImage ? imageBlocked : sendBlocked) && value.trim().length > 0;

  return (
    <div className="shrink-0 border-t border-slate-200 bg-white px-6 py-4 font-sans shadow-sm">
      {(attachedImage ? imageStatus : sendStatus) && <p role="status" className="text-xs text-slate-600 mb-2">{attachedImage ? imageStatus : sendStatus}</p>}
      {hasEarlierImage && !attachedImage && !textFile && !isStreaming && (
        <p className="text-xs text-slate-600 mb-2">Reattach the image to ask another question about it.</p>
      )}
      {hasEarlierTextFile && !attachedImage && !textFile && !isStreaming && (
        <p className="text-xs text-slate-600 mb-2">Attach the .txt file again to ask another question about it.</p>
      )}
      {hasEarlierCsvFile && !attachedImage && !textFile && !isStreaming && (
        <p className="text-xs text-slate-600 mb-2">Attach the .csv file again to ask another question about it.</p>
      )}
      {imageError && (
        <div role="alert" className="mb-2.5 flex items-center justify-between p-2.5 bg-[#ffe4e6] text-[#be123c] font-bold text-xs border-2 border-[#f43f5e]">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            <span>{imageError}</span>
          </div>
          <button aria-label="Dismiss attachment error" onClick={() => setImageError(null)}>
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

      {textFile && (
        <div className="mb-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-slate-800">{textFile.preview?.filename ?? textFile.file.name}</p>
              <p className="text-xs text-slate-600">{textFile.preview?.size_bytes ?? textFile.file.size} bytes · {csvFile ? 'CSV' : 'Text file'}</p>
            </div>
            <button type="button" onClick={removeTextFile} className="text-sm font-medium text-rose-600">
              {csvFile ? 'Remove CSV' : 'Remove text file'}
            </button>
          </div>
          {textFile.state === 'loading' && <p role="status" className="mt-2 text-sm">{csvFile ? 'Checking CSV…' : 'Checking text file…'}</p>}
          {textFile.preview && (
            <>
              {(lineCount > 1 || csvPreview) && (
                <fieldset disabled={textFile.state === 'loading' || isStreaming} className="mt-2 flex flex-wrap items-end gap-2">
                  <legend className="text-xs text-slate-600">{csvFile ? `Choose up to 12 rows from ${lineCount} total` : `Choose lines from ${lineCount} total`}</legend>
                  <label className="text-xs text-slate-700">
                    {csvFile ? 'From row' : 'From line'}
                    <input type="number" min="1" max={lineCount} value={firstLine}
                      onChange={event => setFirstLine(event.target.value)} className="ml-1 w-16 rounded border bg-white p-1" />
                  </label>
                  <label className="text-xs text-slate-700">
                    {csvFile ? 'To row' : 'To line'}
                    <input type="number" min="1" max={lineCount} value={lastLine}
                      onChange={event => setLastLine(event.target.value)} className="ml-1 w-16 rounded border bg-white p-1" />
                  </label>
                  <button type="button" disabled={!validRange} className="rounded border bg-white px-2 py-1 text-xs disabled:opacity-50"
                    onClick={() => validRange && !textUpload.current && loadTextPreview(textFile.file,
                      { start: Number(firstLine), end: Number(lastLine) }, textFile)}>
                    {csvFile ? 'Preview rows' : 'Preview lines'}
                  </button>
                  {(wantsRange || previewedRange) && (
                    <button type="button" className="rounded border bg-white px-2 py-1 text-xs" onClick={() => {
                      if (textUpload.current) return;
                      setFirstLine('');
                      setLastLine('');
                      return loadTextPreview(textFile.file, undefined, textFile);
                    }}>{csvFile ? 'Clear row selection' : 'Use whole file'}</button>
                  )}
                </fieldset>
              )}
              {!rangeMatches && textFile.state === 'ready' && (
                <p role="status" className="mt-2 text-xs text-slate-600">
                  {csvFile ? 'Preview an inclusive range of up to 12 rows before asking a question.' :
                    validRange ? 'Preview the selected lines before sending.' : 'Choose two valid line numbers or use the whole file.'}
                </p>
              )}
              {textFile.state === 'ready' && (
                <>
                  {csvPreview ? <div className="mt-2 max-h-48 overflow-auto rounded border bg-white">
                    <table aria-label="CSV preview" className="min-w-full border-collapse text-left text-xs">
                      <thead><tr><th scope="col" className="border-b px-2 py-1">Row</th>{csvPreview.headers.map((header, index) => (
                        <th key={header} scope="col" className="border-b px-2 py-1">
                          {header} <span className="block font-normal text-slate-600">{csvPreview.column_types[index]}</span>
                        </th>
                      ))}</tr></thead>
                      <tbody>{csvPreview.rows.map((row, index) => (
                        <tr key={index}>
                          <th scope="row" className="border-b px-2 py-1 font-normal">{(previewedRange?.start ?? 1) + index}</th>
                          {row.map((cell, column) => (
                            <td key={column} className="whitespace-pre-wrap break-words border-b px-2 py-1">{cell}</td>
                          ))}
                        </tr>
                      ))}</tbody>
                    </table>
                  </div> : preview && 'text' in preview && (
                    <pre aria-label="Text file preview" className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded border bg-white p-2 text-xs">
                      {preview.text}
                    </pre>
                  )}
                  <p role="status" className="mt-2 text-xs text-slate-600">
                    {previewedRange ? `${csvFile ? 'Rows' : 'Lines'} ${previewedRange.start}-${previewedRange.end}. ` :
                      csvPreview ? `${csvPreview.rows.length} of ${csvPreview.row_count} data rows. ` : 'Whole file. '}
                    {textFile.preview.truncated ? 'Preview shortened. ' : 'Preview ready. '}
                    {csvPreview && 'Column types describe the displayed rows. '}
                    {onSendTextFile ? 'Questions use this scope for this turn only.' :
                      'Questions about this file require the authenticated local chat-only profile.'}
                  </p>
                </>
              )}
            </>
          )}
          {questionTooLong && <p role="status" className="mt-2 text-xs text-rose-600">Keep the question within 1 KiB.</p>}
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
        <input ref={textInputRef} type="file" accept=".txt,.csv,text/plain,text/csv" className="hidden"
          onChange={handleTextFileSelect} id="text-file-input" />

        {/* Attach button */}
        <button
          onClick={handleAttachClick}
          aria-label="Attach image"
          disabled={isStreaming || imageBlocked}
          title={imageStatus || "Attach image"}
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
        <button type="button" disabled={isStreaming} onClick={() => textInputRef.current?.click()}
          aria-label="Attach text or CSV file" title="Preview a .txt or .csv file"
          className="h-11 shrink-0 rounded-lg border border-slate-200 bg-slate-50 px-2.5 text-xs font-semibold text-slate-600 hover:bg-blue-50 disabled:opacity-50">
          TXT / CSV
        </button>

        {/* Text Area */}
        <div className="flex-1 relative">
          <textarea
            ref={textareaRef}
            id="chat-input-textarea"
            aria-label="Message"
            value={value}
            onChange={handleTextChange}
            onKeyDown={handleKeyDown}
            disabled={isStreaming}
            placeholder={
              attachedImage
                ? 'Type a message about the attached image...'
                : csvFile ? 'Ask about the selected rows...' : textFile ? 'Ask a question about the text file...' : 'Type a message...'
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
            aria-label="Start new conversation"
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
