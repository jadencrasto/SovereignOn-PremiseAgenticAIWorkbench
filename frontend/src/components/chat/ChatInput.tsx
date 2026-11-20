/**
 * frontend/src/components/chat/ChatInput.tsx
 * -------------------------------------------
 * Command Dispatcher (White & Light Blue Style)
 */

import React, { useRef, useState, useCallback, useEffect } from 'react';
import { Square, Trash2, Paperclip, X, AlertTriangle, ArrowRight } from 'lucide-react';
import {
  MAX_TEXT_ATTACHMENTS, MAX_TEXT_ATTACHMENT_BYTES, MAX_TEXT_ATTACHMENT_TOTAL_BYTES,
  previewCsvFile, previewJsonFile, previewTextFile, type CsvPreview, type TextFileBatch, type TextFileLineRange, type TextFilePreview,
} from '../../api/chat';
import {
  ALLOWED_IMAGE_TYPES,
  MAX_IMAGE_SIZE_BYTES,
} from '../../types';

interface ChatInputProps {
  onSendMessage: (text: string, image?: File) => void;
  onStopStream: () => void;
  stopDisabled?: boolean;
  isStreaming: boolean;
  sendBlocked: boolean;
  imageBlocked: boolean;
  textOnly?: boolean;
  localImage?: boolean;
  hasEarlierImage?: boolean;
  hasEarlierTextFile?: boolean;
  hasEarlierCsvFile?: boolean;
  hasEarlierJsonFile?: boolean;
  sessionId?: string;
  onSendTextFile?: (question: string, file: File, range?: TextFileLineRange) => void;
  onSendTextFiles?: (question: string, batch: TextFileBatch, range?: TextFileLineRange) => void;
  textFileSupported: boolean;
  onSessionExpired?: () => void;
  sendStatus?: string;
  imageStatus?: string;
  onClearSession: () => void;
}

interface FileDraft {
  file: File;
  source: number;
  state: 'loading' | 'ready' | 'failed';
  preview?: TextFilePreview | CsvPreview;
  range?: TextFileLineRange;
}

function previewError(csv: boolean, range: TextFileLineRange | undefined, status?: number, code?: string) {
  if (status === 413) return 'The file exceeds the 2 KiB or decoded text limit.';
  if (status === 403) return 'File preview is unavailable for this account.';
  if (status === 404) return 'This conversation is no longer available. Open another conversation and attach the file again.';
  if (status === 415) return 'Choose a supported UTF-8 TXT, CSV or JSON file.';
  if (code === 'CSV_RANGE_INVALID' || code === 'TEXT_FILE_RANGE_INVALID' || (range && status === 422)) {
    return csv ? 'Choose an inclusive range of up to 12 data rows.' : 'Choose a line range containing text.';
  }
  if (status === 422) return csv
    ? 'CSV preview failed. Use valid UTF-8 comma-delimited rows with unique headers and up to 32 data rows.'
    : 'Choose a valid UTF-8 text file.';
  return 'The file preview could not be loaded. Select the file again to retry.';
}

export const ChatInput: React.FC<ChatInputProps> = ({
  onSendMessage,
  onStopStream,
  stopDisabled = false,
  isStreaming,
  sendBlocked,
  imageBlocked,
  textOnly,
  localImage,
  hasEarlierImage,
  hasEarlierTextFile,
  hasEarlierCsvFile,
  hasEarlierJsonFile,
  sessionId,
  onSendTextFile,
  onSendTextFiles,
  textFileSupported,
  onSessionExpired,
  sendStatus,
  imageStatus,
  onClearSession,
}) => {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textInputRef = useRef<HTMLInputElement>(null);
  const textUpload = useRef<AbortController | null>(null);
  const confirmedText = useRef<FileDraft | null>(null);

  const [attachedImage, setAttachedImage] = useState<File | null>(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [textFiles, setTextFiles] = useState<File[]>([]);
  const [textFile, setTextFile] = useState<FileDraft | null>(null);
  const [firstLine, setFirstLine] = useState('');
  const [lastLine, setLastLine] = useState('');
  const csvFile = textFile?.file.name.toLowerCase().endsWith('.csv') ?? false;
  const jsonFile = textFile?.file.name.toLowerCase().endsWith('.json') ?? false;
  const tabular = csvFile || jsonFile;
  const rangeUnit = jsonFile ? 'record' : csvFile ? 'row' : 'line';
  const minimumIndex = jsonFile ? 0 : 1;
  const preview = textFile?.preview;
  const csvPreview = preview && 'headers' in preview ? preview : undefined;
  const lineCount = csvPreview?.row_count ?? (preview && 'line_count' in preview ? preview.line_count : 0);
  const wantsRange = Boolean(firstLine || lastLine);
  const validRange = [firstLine, lastLine].every(bound => /^[1-9][0-9]{0,3}$/.test(bound) || (jsonFile && bound === '0')) &&
    Number(firstLine) <= Number(lastLine) && Number(lastLine) <= lineCount - Number(jsonFile) &&
    (!tabular || Number(lastLine) - Number(firstLine) < 12);
  const requestedRange = validRange
    ? { start: Number(firstLine) + Number(jsonFile), end: Number(lastLine) + Number(jsonFile) }
    : undefined;
  const previewedRange = textFile?.range;
  const rangeMatches = wantsRange
    ? requestedRange !== undefined && previewedRange?.start === requestedRange.start && previewedRange.end === requestedRange.end
    : !csvFile && !previewedRange && (!jsonFile || lineCount <= 12);
  const questionTooLong = Boolean(textFiles.length && new TextEncoder().encode(value.trim()).length > 1024);
  const multipleTextFiles = textFiles.length > 1;
  const sendBatch = multipleTextFiles || jsonFile;
  const textFileBlocked = Boolean(textFiles.length &&
    (!textFile || textFile.file !== textFiles[textFile.source - 1] ||
      !textFileSupported || !(sendBatch ? onSendTextFiles : onSendTextFile) ||
      textFile.state !== 'ready' || !rangeMatches || questionTooLong));

  useEffect(() => () => {
    textUpload.current?.abort();
    textUpload.current = null;
    confirmedText.current = null;
  }, []);

  const removeTextFile = useCallback(() => {
    textUpload.current?.abort();
    textUpload.current = null;
    confirmedText.current = null;
    setTextFiles([]);
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
      if (confirmedText.current !== textFile) return;
      if (sendBatch) {
        onSendTextFiles?.(value.trim(), { files: textFiles, source: textFile.source }, textFile.range);
      } else if (textFile.range) onSendTextFile?.(value.trim(), textFile.file, textFile.range);
      else onSendTextFile?.(value.trim(), textFile.file);
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
    textFile, textFiles, sendBatch, textFileBlocked, onSendTextFile, onSendTextFiles, removeTextFile]);

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

    if (!ALLOWED_IMAGE_TYPES.includes(file.type as any) &&
        !(localImage && !file.type && /\.(?:png|jpe?g|webp)$/i.test(file.name))) {
      setAttachedImage(null);
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
      setImagePreviewUrl(null);
      setImageError(`INVALID FORMAT: Allowed PNG, JPEG, WebP.`);
      return;
    }
    const sizeLimit = localImage ? 2 * 1024 * 1024 : MAX_IMAGE_SIZE_BYTES;
    if (file.size === 0 || file.size > sizeLimit) {
      setAttachedImage(null);
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
      setImagePreviewUrl(null);
      const mb = (file.size / (1024 * 1024)).toFixed(1);
      setImageError(file.size === 0 ? 'Image file is empty.' : `Image size ${mb} MB exceeds the ${sizeLimit / 1048576} MB limit.`);
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

  const loadTextPreview = async (file: File, range?: TextFileLineRange, previous?: FileDraft, source = previous?.source ?? 1) => {
    textUpload.current?.abort();
    const upload = new AbortController();
    textUpload.current = upload;
    confirmedText.current = null;
    setImageError(null);
    setTextFile({ ...previous, file, source, state: 'loading' });
    try {
      const name = file.name.toLowerCase();
      const readPreview = name.endsWith('.json') ? previewJsonFile : name.endsWith('.csv') ? previewCsvFile : previewTextFile;
      const preview = await readPreview(file, sessionId, upload.signal, range);
      if (textUpload.current === upload) {
        const ready: FileDraft = { file, source, state: 'ready', preview, range };
        confirmedText.current = ready;
        setTextFile(ready);
      }
    } catch (error) {
      if (textUpload.current !== upload) return;
      const { status, code } = (error ?? {}) as { status?: number; code?: string };
      if (status === 401) {
        removeTextFile();
        setImageError('Your session has expired. Sign in again to preview a file.');
        onSessionExpired?.();
      } else {
        if (status === 404) removeTextFile();
        else setTextFile({ ...previous, file, source, state: 'failed' });
        setImageError(file.name.toLowerCase().endsWith('.json') && status === 422
          ? 'Use valid JSON with unique fields, up to 32 records, and preview at most 12 records per selection.'
          : previewError(file.name.toLowerCase().endsWith('.csv'), range, status, code));
      }
    } finally {
      if (textUpload.current === upload) textUpload.current = null;
    }
  };

  const replaceTextFiles = (files: File[]) => {
    removeTextFile();
    setTextFiles(files);
    if (files.length === 1) return loadTextPreview(files[0]);
  };

  const handleTextFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (!files.length || isStreaming || !textFileSupported) return;
    event.target.value = '';
    removeTextFile();
    handleRemoveImage();
    if (files.length > (onSendTextFiles ? MAX_TEXT_ATTACHMENTS : 1)) {
      setImageError(onSendTextFiles ? 'Choose up to three files.' : 'Choose one file.');
      return;
    }
    for (const file of files) {
      const name = file.name.toLowerCase();
      const media = ['', 'text/plain', 'application/octet-stream'];
      if (name.endsWith('.csv')) media.push('text/csv', 'application/vnd.ms-excel');
      const json = Boolean(onSendTextFiles && name.endsWith('.json'));
      if (json) media.push('application/json');
      if ((!/\.(?:txt|csv)$/.test(name) && !json) || !media.includes(file.type)) {
        setImageError('Choose UTF-8 TXT, CSV or JSON files. PDF, DOCX and XLSX chat attachments are unavailable.');
        return;
      }
      if (!file.size || file.size > MAX_TEXT_ATTACHMENT_BYTES) {
        setImageError(file.size ? 'The text file exceeds 2 KiB.' : 'The text file is empty.');
        return;
      }
    }
    if (files.reduce((total, file) => total + file.size, 0) > MAX_TEXT_ATTACHMENT_TOTAL_BYTES) {
      setImageError('Combined file data must be at most 4 KiB.');
      return;
    }
    setTextFiles(files);
    if (files.length === 1) return loadTextPreview(files[0]);
  };

  const canSend = !isStreaming && !textFileBlocked && !(attachedImage ? imageBlocked : sendBlocked) && value.trim().length > 0;

  return (
    <div className="shrink-0 border-t border-slate-200 bg-white px-6 py-4 font-sans shadow-sm">
      {(attachedImage ? imageStatus : sendStatus) && <p role="status" className="text-xs text-slate-600 mb-2">{attachedImage ? imageStatus : sendStatus}</p>}
      {hasEarlierImage && !attachedImage && !textFiles.length && !isStreaming && (
        <p className="text-xs text-slate-600 mb-2">Reattach the image to ask another question about it.</p>
      )}
      {hasEarlierTextFile && !attachedImage && !textFiles.length && !isStreaming && (
        <p className="text-xs text-slate-600 mb-2">Reattach the TXT file and include any lines or passage you want to use.</p>
      )}
      {hasEarlierCsvFile && !attachedImage && !textFiles.length && !isStreaming && (
        <p className="text-xs text-slate-600 mb-2">Reattach the CSV and restate any filters or row selections in your question.</p>
      )}
      {hasEarlierJsonFile && !attachedImage && !textFiles.length && !isStreaming && (
        <p className="text-xs text-slate-600 mb-2">Reattach the JSON and restate any filters or record selections in your question.</p>
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

      {multipleTextFiles && (
        <fieldset disabled={isStreaming} className="mb-3 rounded-lg border border-slate-200 bg-slate-50 px-3 pb-2">
          <legend className="px-1 text-xs font-semibold text-slate-700">Choose a source to preview and analyze</legend>
          {textFiles.some(file => file.name.toLowerCase().endsWith('.csv')) &&
            textFiles.some(file => file.name.toLowerCase().endsWith('.txt')) && (
              <p className="text-xs text-slate-600">
                To check limits, select the CSV and ask “Compare the CSV against the TXT file.”
                Use source numbers if names repeat. Reattach both files for another comparison.
              </p>
          )}
          {textFiles.some(file => file.name.toLowerCase().endsWith('.json')) &&
            textFiles.some(file => file.name.toLowerCase().endsWith('.txt')) && (
              <p className="text-xs text-slate-600">
                Select the JSON and ask "Compare the JSON against the TXT file."
                Use source numbers if names repeat. Reattach both files for another comparison.
              </p>
          )}
          <ul className="divide-y divide-slate-200">
            {textFiles.map((file, index) => (
              <li key={index} className="flex items-center justify-between gap-3 py-2 text-xs">
                <label className="flex min-w-0 items-center gap-2">
                  <input type="radio" name="text-source" checked={textFile?.source === index + 1}
                    onChange={() => {
                      setFirstLine('');
                      setLastLine('');
                      return loadTextPreview(file, undefined, undefined, index + 1);
                    }} />
                  <span className="break-all">Source {index + 1} · {file.name}</span>
                </label>
                <button type="button" className="shrink-0 text-rose-600"
                  aria-label={'Remove source ' + (index + 1)}
                  onClick={() => replaceTextFiles(textFiles.filter((_, item) => item !== index))}>Remove</button>
              </li>
            ))}
          </ul>
        </fieldset>
      )}
      {textFile && (
        <div className="mb-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="break-all text-sm font-semibold text-slate-800">{textFile.preview?.filename ?? textFile.file.name}</p>
              <p className="text-xs text-slate-600">{textFile.preview?.size_bytes ?? textFile.file.size} bytes · {jsonFile ? 'JSON' : csvFile ? 'CSV' : 'Text file'}</p>
            </div>
            {!multipleTextFiles && <button type="button" onClick={removeTextFile} className="text-sm font-medium text-rose-600">
              {jsonFile ? 'Remove JSON' : csvFile ? 'Remove CSV' : 'Remove text file'}
            </button>}
          </div>
          {textFile.state === 'loading' && <p role="status" className="mt-2 text-sm">
            {jsonFile ? 'Checking JSON…' : csvFile ? 'Checking CSV…' : 'Checking text file…'}
          </p>}
          {textFile.preview && (
            <>
              {(lineCount > 1 || csvPreview) && (
                <fieldset disabled={textFile.state === 'loading' || isStreaming} className="mt-2 flex flex-wrap items-end gap-2">
                  <legend className="text-xs text-slate-600">{tabular ? `Choose up to 12 ${rangeUnit}s from ${lineCount} total` : `Choose lines from ${lineCount} total`}</legend>
                  <label className="text-xs text-slate-700">
                    {jsonFile ? 'From record index' : `From ${rangeUnit}`}
                    <input type="number" min={minimumIndex} max={lineCount - Number(jsonFile)} value={firstLine}
                      onChange={event => setFirstLine(event.target.value)} className="ml-1 w-16 rounded border bg-white p-1" />
                  </label>
                  <label className="text-xs text-slate-700">
                    {jsonFile ? 'To record index' : `To ${rangeUnit}`}
                    <input type="number" min={minimumIndex} max={lineCount - Number(jsonFile)} value={lastLine}
                      onChange={event => setLastLine(event.target.value)} className="ml-1 w-16 rounded border bg-white p-1" />
                  </label>
                  <button type="button" disabled={!validRange} className="rounded border bg-white px-2 py-1 text-xs disabled:opacity-50"
                    onClick={() => requestedRange && !textUpload.current && loadTextPreview(textFile.file, requestedRange, textFile)}>
                    {`Preview ${rangeUnit}s`}
                  </button>
                  {(wantsRange || previewedRange) && (
                    <button type="button" className="rounded border bg-white px-2 py-1 text-xs" onClick={() => {
                      if (textUpload.current) return;
                      setFirstLine('');
                      setLastLine('');
                      return loadTextPreview(textFile.file, undefined, textFile);
                    }}>{jsonFile ? 'Clear record selection' : csvFile ? 'Clear row selection' : 'Use whole file'}</button>
                  )}
                </fieldset>
              )}
              {!rangeMatches && textFile.state === 'ready' && (
                <p role="status" className="mt-2 text-xs text-slate-600">
                  {tabular ? `Preview an inclusive range of up to 12 ${rangeUnit}s before asking a question.` :
                    validRange ? 'Preview the selected lines before sending.' : 'Choose two valid line numbers or use the whole file.'}
                </p>
              )}
              {textFile.state === 'ready' && (
                <>
                  {csvPreview ? <div tabIndex={0} role="region" aria-label={jsonFile ? 'Scrollable JSON preview' : 'Scrollable CSV preview'}
                    className="mt-2 max-h-48 max-w-full overflow-auto rounded border bg-white">
                    <table aria-label={jsonFile ? 'JSON preview' : 'CSV preview'} className="min-w-full border-collapse text-left text-xs">
                      <thead><tr><th scope="col" className="border-b px-2 py-1">{jsonFile ? 'Record index' : 'Row'}</th>{csvPreview.headers.map((header, index) => (
                        <th key={header} scope="col" className="border-b px-2 py-1">
                          {header} <span className="block font-normal text-slate-600">{csvPreview.column_types[index]}</span>
                        </th>
                      ))}</tr></thead>
                      <tbody>{csvPreview.rows.map((row, index) => (
                        <tr key={index}>
                          <th scope="row" className="border-b px-2 py-1 font-normal">{(previewedRange?.start ?? 1) + index - Number(jsonFile)}</th>
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
                    {jsonFile ? 'Record indices start at 0. ' :
                      previewedRange ? `${csvFile ? 'Rows' : 'Lines'} ${previewedRange.start}-${previewedRange.end}. ` :
                      csvPreview ? `${csvPreview.rows.length} of ${csvPreview.row_count} data rows. ` : 'Whole file. '}
                    {textFile.preview.truncated ? 'Preview shortened. ' : 'Preview ready. '}
                    {csvPreview && (jsonFile ? 'Field types describe the displayed records. ' : 'Column types describe the displayed rows. ')}
                    {(sendBatch ? onSendTextFiles : onSendTextFile) ? (rangeMatches ? 'This selection applies to the next question only.' : '') :
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
        {(!textOnly || localImage) && <>
        <input
          ref={fileInputRef}
          type="file"
          accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"
          className="hidden"
          onChange={handleFileSelect}
          id="image-file-input"
        />
        {!textOnly && <input ref={textInputRef} type="file" accept={onSendTextFiles ? '.txt,.csv,.json,text/plain,text/csv,application/json' : '.txt,.csv,text/plain,text/csv'} className="hidden"
          multiple={onSendTextFiles ? true : undefined}
          onChange={handleTextFileSelect} id="text-file-input" />}

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
        {!textOnly && <button type="button" disabled={isStreaming || !textFileSupported} onClick={() => textInputRef.current?.click()}
          aria-label={onSendTextFiles ? 'Attach TXT, CSV or JSON files' : 'Attach text or CSV file'} aria-describedby="chat-file-support"
          className="h-11 shrink-0 rounded-lg border border-slate-200 bg-slate-50 px-2.5 text-xs font-semibold text-slate-600 hover:bg-blue-50 disabled:opacity-50">
          {onSendTextFiles ? 'TXT / CSV / JSON' : 'TXT / CSV'}
        </button>}
        </>}

        {/* Text Area */}
        <div className="relative min-w-0 flex-1">
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
              disabled={stopDisabled}
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
        {localImage && <span className="text-slate-600">
          {imageBlocked && imageStatus ? imageStatus : 'Local images · PNG, JPEG and WebP · up to 2 MiB'}
        </span>}
        {!textOnly && <span id="chat-file-support" className="text-slate-600">
          {textFileSupported ? (onSendTextFiles
            ? 'Pick up to three TXT, CSV or JSON files, 2 KiB each and 4 KiB total. A new pick replaces the set.'
            : 'One UTF-8 TXT or CSV per request, up to 2 KiB.') :
            'TXT, CSV and JSON require authenticated local chat mode.'}{' '}
          PDF, DOCX and XLSX chat attachments are unavailable.
        </span>}
      </div>
    </div>
  );
};
