import { useCallback, useEffect, useRef, useState } from 'react';
import { apiRequest } from '../../api/client';

interface Source {
  source_id: string;
  label: string;
  revision: number;
  passages: number;
  document?: { format: string; status: 'ready' | 'partial'; warnings: string[] };
}

interface Corpus {
  mode: 'bounded_lexical';
  retention: 'disposable' | 'durable';
  max_sources: number;
  max_source_bytes: number;
  document_formats?: string[];
  max_document_bytes?: number;
  sources: Source[];
}

const endpoint = '/api/chat/local-knowledge';
const mediaTypes: Record<string, string> = {
  txt: 'text/plain',
  md: 'text/markdown',
  pdf: 'application/pdf',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
};

export function LocalKnowledgePanel({ available, active, setActive, selection, setSelection, onRevision }: {
  available: boolean;
  active: boolean;
  setActive: (active: boolean) => void;
  selection: string[] | null;
  setSelection: (selection: string[] | null) => void;
  onRevision: () => void;
}) {
  const [corpus, setCorpus] = useState<Corpus>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [operation, setOperation] = useState('');
  const channel = useRef<BroadcastChannel | undefined>(undefined);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const report = await apiRequest<Corpus>(endpoint, { signal, cache: 'no-store' });
      if (!signal?.aborted) {
        setCorpus(report);
        setError('');
      }
    } catch {
      if (!signal?.aborted) setError('Local sources could not be loaded.');
    }
  }, []);

  useEffect(() => {
    if (!available) return;
    const controller = new AbortController();
    void refresh(controller.signal);
    const update = () => {
      onRevision();
      void refresh(controller.signal);
    };
    if (typeof BroadcastChannel !== 'undefined') {
      channel.current = new BroadcastChannel('sovereign-local-knowledge');
      channel.current.onmessage = update;
    }
    window.addEventListener('focus', update);
    return () => {
      controller.abort();
      channel.current?.close();
      channel.current = undefined;
      window.removeEventListener('focus', update);
    };
  }, [available, onRevision, refresh]);

  const formats = ['txt', 'md', ...(corpus?.document_formats ?? [])];
  const accept = formats.map(format => `.${format}`).join(',');
  const change = async (file: File | null, source?: Source) => {
    if (busy || (!file && !source)) return;
    const format = file?.name.split('.').pop()?.toLowerCase() ?? '';
    if (file) {
      if (!formats.includes(format) || !Object.hasOwn(mediaTypes, format)) {
        setError('Choose one of the supported file formats.');
        return;
      }
      const maxBytes = ['txt', 'md'].includes(format) ? corpus?.max_source_bytes : corpus?.max_document_bytes;
      if (maxBytes !== undefined && file.size > maxBytes) {
        setError(`Choose a ${format.toUpperCase()} file of at most ${maxBytes / 1024} KiB.`);
        return;
      }
    }
    setBusy(true);
    setOperation(file ? `Uploading and indexing ${file.name}…` : 'Removing source…');
    setError('');
    const params = new URLSearchParams();
    if (file) params.set('filename', file.name);
    if (source) params.set('revision', String(source.revision));
    const path = endpoint + '/sources' + (source ? '/' + encodeURIComponent(source.source_id) : '');
    try {
      await apiRequest(path + '?' + params, file ? {
        method: source ? 'PUT' : 'POST',
        body: file,
        headers: { 'Content-Type': mediaTypes[format] },
      } : { method: 'DELETE' });
      onRevision();
      channel.current?.postMessage('changed');
      await refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'The local source could not be changed.');
    } finally {
      setBusy(false);
      setOperation('');
    }
  };

  return (
    <details className="border-t border-slate-200 px-5 py-2 text-sm text-slate-700">
      <summary className="cursor-pointer font-medium">
        Local knowledge · {available ? `${corpus?.sources.length ?? '…'} sources${active ? ' · active' : ''}` : 'unavailable'}
      </summary>
      {!available && active && <button type="button" className="mt-2 text-xs underline" onClick={() => setActive(false)}>
        Use general chat
      </button>}
      {available && (
        <div className="mt-2 max-h-56 space-y-2 overflow-y-auto text-xs">
          <p>Supported formats: {formats.map(format => format.toUpperCase()).join(', ')}.</p>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={active} onChange={event => setActive(event.target.checked)} />
            Answer from local sources
          </label>
          {active && <fieldset className="flex flex-wrap gap-3">
            <legend className="font-medium">Sources for the next question</legend>
            <label className="flex items-center gap-1">
              <input type="radio" checked={selection === null} onChange={() => setSelection(null)} />
              All my registered local sources
            </label>
            <label className="flex items-center gap-1">
              <input type="radio" checked={selection !== null} onChange={() => setSelection([])} />
              Selected sources
            </label>
            {selection?.length === 0 && <span role="status">Choose at least one registered source.</span>}
            {corpus && selection?.some(id => !corpus.sources.some(source => source.source_id === id)) &&
              <button type="button" className="underline" onClick={() => setSelection(
                selection.filter(id => corpus.sources.some(source => source.source_id === id)),
              )}>Clear removed selections</button>}
          </fieldset>}
          {corpus && <p>{corpus.sources.length} of {corpus.max_sources} sources · {corpus.retention} retention</p>}
          <label className="flex flex-wrap items-center gap-2">
            Import an authorized local file
            <input type="file" accept={accept} disabled={busy || !corpus}
              onChange={event => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) void change(file);
              }} />
          </label>
          {operation && <p role="status">{operation}</p>}
          <ul className="space-y-2">
            {corpus?.sources.map(source => (
              <li key={source.source_id} className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-2">
                {active && selection !== null && <input type="checkbox" aria-label={`Select ${source.label}`}
                  checked={selection.includes(source.source_id)} onChange={event => setSelection(event.target.checked
                    ? [...selection, source.source_id] : selection.filter(id => id !== source.source_id))} />}
                <span className="break-all font-medium">{source.label}</span>
                <span>Revision {source.revision} · {source.passages} passages</span>
                {source.document && <span>{source.document.format.toUpperCase()} · {
                  source.document.status === 'partial' ? 'Partially extracted' : 'Indexed'
                }</span>}
                {!!source.document?.warnings.length && <ul className="basis-full text-amber-800">
                  {source.document.warnings.map(warning => <li key={warning}>{warning}</li>)}
                </ul>}
                <label className="cursor-pointer underline">
                  Replace and reindex
                  <input type="file" className="sr-only" accept={accept} disabled={busy} aria-label={`Replace ${source.label}`}
                    onChange={event => {
                      const file = event.target.files?.[0];
                      event.target.value = '';
                      if (file) void change(file, source);
                    }} />
                </label>
                <button type="button" className="underline" disabled={busy} onClick={() => void change(null, source)}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
          {error && <p role="alert">{error}</p>}
        </div>
      )}
    </details>
  );
}
