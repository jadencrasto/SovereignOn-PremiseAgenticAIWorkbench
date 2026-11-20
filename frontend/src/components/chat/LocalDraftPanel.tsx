import { useEffect, useRef, useState } from 'react';
import { apiRequest } from '../../api/client';
import type { ChatMessage, LocalKnowledgeResult } from '../../types';

type Finding = {
  topic: string;
  text: string;
  kind: 'source_value' | 'source_excerpt' | 'visual_interpretation' | 'comparison' | 'missing' | 'user_edited' | 'stale';
  coverage: string;
  source?: LocalKnowledgeResult['citations'][number];
  field?: NonNullable<NonNullable<LocalKnowledgeResult['coverage']>[number]['fields']>[number];
  image?: { filename: string; sha256: string; model: string };
};

type Draft = {
  id: string;
  turn_id?: string;
  revision: number;
  format: 'docx' | 'xlsx' | 'pdf';
  title: string;
  summary: string;
  state: 'draft' | 'approved' | 'exported' | 'stale' | 'cancelled';
  exporting: boolean;
  workflow_steps?: ('retrieve' | 'vision' | 'vision_unavailable' | 'draft')[];
  findings: Finding[];
};

const statusLabels = {
  source_value: 'Source-attributed value',
  source_excerpt: 'Source excerpt',
  visual_interpretation: 'Model-generated visual interpretation',
  comparison: 'Comparison of cited values',
  missing: 'Insufficient evidence',
  user_edited: 'User-edited wording, unverified',
  stale: 'Evidence unavailable',
};

const workflowLabels = {
  retrieve: 'Source evidence checked',
  vision: 'Image analyzed',
  vision_unavailable: 'Image analysis unavailable',
  draft: 'Draft assembled',
};

function location(finding: Finding) {
  const source = finding.source;
  if (!source) return '';
  const locator = finding.field?.document_locator ?? source.document_locator;
  if (!locator) return `line ${source.line}, span ${source.start}–${source.end}`;
  if (locator.format === 'xlsx') return `${locator.sheet}!${locator.cell ?? locator.range ?? ''}`;
  if (locator.format === 'pdf') return `page ${locator.page}${locator.block ? `, block ${locator.block}` : ''}`;
  if (locator.paragraph) return `paragraph ${locator.paragraph}`;
  return `table ${locator.table}, row ${locator.row}${locator.column ? `, column ${locator.column}` : ''}`;
}

type Props = {
  sessionId: string;
  messages: ChatMessage[];
  refreshKey: string;
  selected: string[];
  onSelection: (ids: string[]) => void;
  reviewTurnId?: string;
};

type Edit = { id: string; revision: number; title: string; summary: string; wording: Record<number, string> };

export function LocalDraftPanel({ sessionId, messages, refreshKey, selected, onSelection, reviewTurnId }: Props) {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [edit, setEdit] = useState<Edit>();
  const [action, setAction] = useState('');
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const generation = useRef(0);
  const base = `/api/chat/sessions/${encodeURIComponent(sessionId)}/drafts`;
  useEffect(() => {
    const controller = new AbortController();
    const version = ++generation.current;
    void apiRequest<{ drafts: Draft[] }>(base, { signal: controller.signal }).then(result => {
      if (generation.current === version) setDrafts(result.drafts);
    }).catch(() => {
      if (!controller.signal.aborted && generation.current === version) {
        setDrafts([]);
        setError('Local drafts could not be loaded.');
      }
    });
    return () => controller.abort();
  }, [base, refreshKey, reload]);

  const evidence = messages.flatMap((message, index) => {
    const user = messages[index - 1];
    if (message.role !== 'assistant' || message.withheld || user?.localTurn?.state !== 'completed'
      || user.localTurn.assistant_id !== message.id) return [];
    if (message.localKnowledge && message.localKnowledge.state !== 'unavailable') {
      return [{ id: user.id, label: user.content }];
    }
    if (message.localVisual && user.attachments?.some(file => file.localAvailable)) {
      return [{ id: user.id, label: `Image analysis · ${user.attachments[0].filename}` }];
    }
    return [];
  }).reverse().slice(0, 10);

  async function change(draft: Draft, operation: 'edit' | 'approve' | 'export' | 'cancel-export' | 'cancel-workflow' | 'remove') {
    if (action && operation !== 'cancel-export' && operation !== 'cancel-workflow') return;
    const requestPath = `${base}/${encodeURIComponent(draft.id)}`;
    const url = operation === 'edit' ? requestPath : operation === 'remove'
      ? `${requestPath}?revision=${draft.revision}` : `${requestPath}/${operation}`;
    const method = operation === 'edit' ? 'PATCH' : operation === 'remove' ? 'DELETE' : 'POST';
    const body = operation === 'edit' && edit
      ? { revision: edit.revision, title: edit.title, summary: edit.summary, wording: edit.wording }
      : { revision: draft.revision };
    if (operation !== 'cancel-export') setAction(`${operation}:${draft.id}`);
    setError('');
    generation.current++;
    try {
      const result = await apiRequest<Draft>(url, { method, ...(method === 'DELETE' ? {} : { body: JSON.stringify(body) }) });
      if (operation === 'remove') setDrafts(current => current.filter(row => row.id !== draft.id));
      else setDrafts(current => current.map(row => row.id === draft.id && row.revision <= result.revision ? result : row));
      if (operation === 'edit') setEdit(undefined);
    } catch (error) {
      setError(error instanceof Error ? error.message : 'The draft operation failed.');
      try {
        const version = ++generation.current;
        const result = await apiRequest<{ drafts: Draft[] }>(base);
        if (version === generation.current) setDrafts(result.drafts);
      } catch {
        setDrafts([]);
      }
    } finally {
      if (operation !== 'cancel-export') setAction('');
    }
  }

  return (
    <details open={drafts.some(draft => draft.turn_id && draft.turn_id === reviewTurnId) || undefined}
      className="shrink-0 border-t border-slate-200 px-5 py-2 text-sm text-slate-700">
      <summary className="cursor-pointer font-medium">Drafts{drafts.length ? ` (${drafts.length})` : ''}
        {drafts.some(draft => draft.state === 'draft') && ' · Awaiting review'}
      </summary>
      <div className="max-h-[40vh] space-y-3 overflow-y-auto py-2">
        <p className="text-xs">Ask chat to prepare a report or spreadsheet. The latest knowledge answer is used by default. Select an image analysis below to include it.</p>
        <button type="button" className="text-xs underline" disabled={!!action}
          onClick={() => { setError(''); setReload(value => value + 1); }}>Refresh drafts</button>
        {evidence.length > 0 && <fieldset className="space-y-1 text-xs">
          <legend className="font-medium">Evidence turns (up to five)</legend>
          {evidence.map(row => <label key={row.id} className="flex items-baseline gap-2">
            <input type="checkbox" checked={selected.includes(row.id)}
              disabled={!selected.includes(row.id) && selected.length >= 5}
              onChange={event => onSelection(event.target.checked ? [...selected, row.id] : selected.filter(id => id !== row.id))} />
            <span className="truncate">{row.label}</span>
          </label>)}
          {selected.length > 0 && <button type="button" className="underline" onClick={() => onSelection([])}>Clear evidence selection</button>}
        </fieldset>}
        {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
        {drafts.map(draft => {
          const editing = edit?.id === draft.id ? edit : undefined;
          const exporting = action === `export:${draft.id}` || draft.exporting;
          return <details key={draft.id} open={draft.turn_id && draft.turn_id === reviewTurnId || undefined}
            className="rounded border border-slate-200 p-2">
            <summary className="cursor-pointer break-words font-medium">
              {draft.title} · {draft.format.toUpperCase()} · revision {draft.revision} · {draft.state}
            </summary>
            {draft.workflow_steps && <p className="my-2 text-xs text-slate-600">
              {draft.state === 'cancelled' ? 'Workflow cancelled.' : draft.state === 'stale' ? 'Evidence changed. Start a new workflow.' :
                [...draft.workflow_steps.map(step => workflowLabels[step]),
                  draft.state === 'exported' ? 'Export ready' : draft.exporting ? 'Exporting' :
                    draft.state === 'approved' ? 'Ready to export' : 'Awaiting your approval'].join(' · ')}
            </p>}
            {editing ? <div className="my-2 grid gap-2">
              <label className="grid">Title
                <input className="rounded border px-2 py-1" maxLength={160} value={editing.title}
                  onChange={event => setEdit({...editing, title: event.target.value})} />
              </label>
              <label className="grid">Executive summary
                <textarea className="rounded border p-2" maxLength={2000} value={editing.summary}
                  onChange={event => setEdit({...editing, summary: event.target.value})} />
              </label>
            </div> : <p className="my-2 whitespace-pre-wrap break-words text-xs">{draft.summary}</p>}
            <ol className="space-y-2">
              {draft.findings.map((finding, index) => <li key={index} className="rounded bg-slate-50 p-2 text-xs">
                <p className="font-medium">{finding.topic}</p>
                <p>{statusLabels[finding.kind]}{finding.coverage === 'conflict' && finding.kind !== 'user_edited' && ' · Conflicting evidence'}</p>
                <p className="whitespace-pre-wrap break-words">{finding.text}</p>
                {editing && <label className="mt-1 grid">Replace wording
                  <input className="rounded border px-2 py-1" maxLength={2000} value={editing.wording[index] ?? ''}
                    onChange={event => {
                      const wording = {...editing.wording};
                      if (event.target.value) wording[index] = event.target.value;
                      else delete wording[index];
                      setEdit({...editing, wording});
                    }} />
                </label>}
                {finding.source && <details className="mt-1">
                  <summary className="cursor-pointer break-words">{finding.kind === 'user_edited' ? 'Original reference · ' : ''}
                    {finding.source.label} · {location(finding)} · revision {finding.source.revision}</summary>
                  {finding.field?.attribute && <p className="mt-1 break-words">
                    {[finding.field.header || finding.field.attribute.replaceAll('_', ' '),
                      ...(finding.field.equipment ?? []), ...(finding.field.conditions ?? [])].join(' · ')}
                  </p>}
                  {finding.field?.value && <p className="break-words">Source value · {finding.field.value}
                    {finding.field.unit && !finding.field.value.endsWith(finding.field.unit) ? ` ${finding.field.unit}` : ''}
                  </p>}
                  <p className="whitespace-pre-wrap break-words">{finding.source.excerpt}</p>
                </details>}
                {finding.image && <details className="mt-1">
                  <summary className="cursor-pointer break-words">{finding.image.filename} · {finding.image.model}</summary>
                  <p className="break-all">SHA-256 {finding.image.sha256}</p>
                </details>}
              </li>)}
            </ol>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
              {editing ? <>
                <button type="button" className="underline" disabled={!!action}
                  onClick={() => void change(draft, 'edit')}>Save revision</button>
                <button type="button" className="underline" onClick={() => setEdit(undefined)}>Cancel edits</button>
              </> : <>
                {draft.state !== 'stale' && draft.state !== 'cancelled' && <>
                  <button type="button" disabled={!!action || exporting} className="underline"
                    onClick={() => setEdit({id: draft.id, revision: draft.revision, title: draft.title, summary: draft.summary, wording: {}})}>Edit</button>
                  {draft.state === 'draft' && <button type="button" disabled={!!action} className="underline"
                    onClick={() => void change(draft, 'approve')}>Approve revision {draft.revision}</button>}
                  {draft.state === 'approved' && !exporting && <button type="button" disabled={!!action} className="underline"
                    onClick={() => void change(draft, 'export')}>Export locally</button>}
                  {draft.state === 'exported' && <a className="underline"
                    href={`${base}/${encodeURIComponent(draft.id)}/download?revision=${draft.revision}`}>Download {draft.format.toUpperCase()}</a>}
                </>}
                {draft.state === 'stale' && <p role="status">A source or image changed. Prepare a new draft from current evidence.</p>}
                {draft.workflow_steps && ['draft', 'approved'].includes(draft.state) &&
                  <button type="button" className="underline" onClick={() => void change(draft, 'cancel-workflow')}>Cancel workflow</button>}
                {!exporting && <button type="button" disabled={!!action} className="underline"
                  onClick={() => void change(draft, 'remove')}>Remove draft</button>}
              </>}
              {exporting && <>
                <span role="status">Generating local export.</span>
                <button type="button" className="underline" onClick={() => void change(draft, 'cancel-export')}>Cancel export</button>
              </>}
            </div>
          </details>;
        })}
      </div>
    </details>
  );
}
