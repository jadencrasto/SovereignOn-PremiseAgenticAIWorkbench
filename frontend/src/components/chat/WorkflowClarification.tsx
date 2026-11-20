import { useState } from 'react';
import type { LocalClarification } from '../../types';

export function WorkflowClarification({
  clarification, busy, resume, cancel, upload, uploading, uploadError, retryUpload, cancelUpload, previewImage,
}: {
  clarification: LocalClarification;
  busy: boolean;
  resume: (selected: string[]) => void;
  cancel: () => void;
  upload?: (file: File) => void;
  uploading?: boolean;
  uploadError?: string;
  retryUpload?: () => void;
  cancelUpload?: () => void;
  previewImage?: (id: string) => string;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const { reason, count, options, question } = clarification;
  const acceptsFile = upload && (reason === 'sources' || reason === 'image');
  const image = reason === 'image' && options.find(option => selected.includes(option.id));
  return <fieldset disabled={busy} className="mx-5 my-2 rounded border border-slate-200 px-3 py-2 text-sm">
    <legend className="px-1 font-medium">{question}</legend>
    {options.length < count && <p>{acceptsFile ? 'Add the missing file, then confirm your selection.' : 'There aren’t enough available choices.'}</p>}
    <div className="flex flex-wrap gap-3">
      {options.map(option => <label key={option.id} className="flex items-center gap-1">
        <input type="checkbox" checked={selected.includes(option.id)}
          disabled={!selected.includes(option.id) && selected.length >= count && count > 1}
          onChange={event => setSelected(previous => event.target.checked
            ? count === 1 ? [option.id] : [...previous, option.id]
            : previous.filter(value => value !== option.id))} />
        {option.label}
      </label>)}
    </div>
    {image && previewImage && <img src={previewImage(image.id)} alt={image.label}
      className="mt-2 max-h-28 max-w-48 rounded border border-slate-200 object-contain" />}
    {acceptsFile && <div className="mt-2 flex flex-wrap items-center gap-3">
      <label className="font-medium underline">
        {reason === 'image' ? 'Upload an image' : 'Upload a document'}
        <input className="sr-only" type="file" disabled={uploading}
          accept={reason === 'image' ? '.png,.jpg,.jpeg,.webp' : '.txt,.md,.pdf,.docx,.xlsx'}
          onChange={event => {
            const file = event.currentTarget.files?.[0];
            event.currentTarget.value = '';
            if (file) upload(file);
          }} />
      </label>
      {uploading && <><span role="status">Uploading and validating file…</span>
        <button type="button" className="underline" onClick={cancelUpload}>Cancel upload</button></>}
      {uploadError && <span role="alert">{uploadError}</span>}
      {retryUpload && !uploading && <button type="button" className="underline" onClick={retryUpload}>Retry upload</button>}
    </div>}
    <div className="mt-2 flex gap-3">
      <button type="button" disabled={uploading || selected.length !== count} className="font-medium underline"
        onClick={() => resume(selected)}>Continue workflow</button>
      <button type="button" className="underline" onClick={cancel}>Cancel workflow</button>
    </div>
  </fieldset>;
}
