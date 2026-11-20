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
  revision_turn_id?: string;
  revision: number;
  format: 'docx' | 'xlsx' | 'pdf';
  title: string;
  summary: string;
  state: 'draft' | 'approved' | 'exported' | 'stale' | 'cancelled';
  exporting: boolean;
  pending_revision?: boolean;
  history?: number[];
  available_formats?: string[];
  change?: { parent: number; operation: string; created_at: string; sections: string[]; added: number; removed: number; unverified: number };
  layout?: { sections: { id: string; heading: string; wording?: string }[]; columns: string[]; comparison: boolean; inline_references: boolean };
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
  reviewDraftId?: string;
  draftTarget?: { id: string; revision: number };
  onDraftTarget?: (target?: { id: string; revision: number }) => void;
};

type Edit = { id: string; revision: number; title: string; summary: string; wording: Record<number, string> };

export function LocalDraftPanel({ sessionId, messages, refreshKey, selected, onSelection,
  reviewTurnId, reviewDraftId, draftTarget, onDraftTarget }: Props) {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [edit, setEdit] = useState<Edit>();
  const [action, setAction] = useState('');
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const generation = useRef(0);
  const [historical, setHistorical] = useState<{ id: string; revision: number; preview?: Draft }>();
  const historyRequest = useRef(0);
  const base = `/api/chat/sessions/${encodeURIComponent(sessionId)}/drafts`;
  useEffect(() => {
    const controller = new AbortController();
    const version = ++generation.current;
    historyRequest.current++;
    setHistorical(undefined);
    void apiRequest<{ drafts: Draft[] }>(base, { signal: controller.signal }).then(result => {
      if (generation.current === version) {
        setDrafts(result.drafts);
        const ownRevision = result.drafts.find(row => row.id === draftTarget?.id && row.revision_turn_id &&
          row.revision_turn_id === reviewTurnId);
        if (ownRevision && ownRevision.revision !== draftTarget?.revision) {
          onDraftTarget?.({ id: ownRevision.id, revision: ownRevision.revision });
        }
      }
    }).catch(() => {
      if (!controller.signal.aborted && generation.current === version) {
        setDrafts([]);
        setError('Local drafts could not be loaded.');
      }
    });
    return () => controller.abort();
  }, [base, refreshKey, reload]);

  async function showRevision(draft: Draft, revision: number) {
    const version = ++historyRequest.current;
    if (revision === draft.revision) {
      setHistorical(undefined);
      return;
    }
    setHistorical({ id: draft.id, revision });
    setError('');
    try {
      const preview = await apiRequest<Draft>(`${base}/${encodeURIComponent(draft.id)}/revisions/${revision}`);
      if (historyRequest.current === version) setHistorical({ id: draft.id, revision, preview });
    } catch (error) {
      if (historyRequest.current !== version) return;
      setHistorical(undefined);
      setError(error instanceof Error ? error.message : 'The earlier draft revision could not be loaded.');
    }
  }

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

  const reviewing = (draft: Draft) => reviewDraftId ? draft.id === reviewDraftId :
    Boolean(reviewTurnId && [draft.turn_id, draft.revision_turn_id].includes(reviewTurnId));
  return (
    <details open={drafts.some(reviewing) || undefined}
      className="shrink-0 border-t border-slate-200 px-5 py-2 text-sm text-slate-700">
      <summary className="cursor-pointer font-medium">Drafts{drafts.length ? ` (${drafts.length})` : ''}
        {drafts.some(draft => draft.state === 'draft') && ' · Awaiting review'}
      </summary>
      <div className="max-h-[40vh] space-y-3 overflow-y-auto py-2">
        <p className="text-xs">Ask chat to prepare a report or spreadsheet. The latest knowledge answer is used by default. Select an image analysis below to include it.</p>
        {draftTarget && <p className="text-xs">Chat is targeting the selected draft at revision {draftTarget.revision}.{' '}
          <button type="button" className="underline" onClick={() => onDraftTarget?.()}>Clear draft target</button>
        </p>}
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
          const prior = historical?.id === draft.id ? historical : undefined;
          return <details key={draft.id} open={reviewing(draft) || undefined}
            className="rounded border border-slate-200 p-2">
            <summary className="cursor-pointer break-words font-medium">
              {draft.title} · {draft.format.toUpperCase()} · revision {draft.revision} · {draft.state}
            </summary>
            {draft.pending_revision && <p role="status" className="my-2 text-xs">A refinement was interrupted. Retry the chat turn to finish it.</p>}
            {draft.change && <p className="my-2 text-xs">
              Previous revision {draft.change.parent} · {draft.change.operation.replaceAll('_', ' ')}
              {draft.change.sections.length > 0 && ` · ${draft.change.sections.join(', ')}`}
              {` · ${draft.change.added} findings added or changed · ${draft.change.removed} removed or replaced · ${draft.change.unverified} edited findings unverified`}
            </p>}
            {!!draft.history?.length && !draft.pending_revision && <label className="flex gap-2 text-xs">Compare with earlier revision
              <select value={prior?.revision ?? draft.revision} onChange={event => void showRevision(draft, Number(event.target.value))}>
                <option value={draft.revision}>Current revision</option>
                {[...draft.history].reverse().map(revision => <option key={revision} value={revision}>Revision {revision}</option>)}
              </select>
            </label>}
            {prior && <section className="my-2 rounded border p-2 text-xs">
              {prior.preview ? <>
                <h4 className="font-medium">Revision {prior.revision} · {prior.preview.state} · read-only</h4>
                <p>{prior.preview.title} · {prior.preview.format.toUpperCase()}</p>
                <p>Summary wording is unverified. {prior.preview.summary}</p>
                <ol>{prior.preview.findings.map((row, index) => <li key={index} className="break-words">
                  {index + 1}. {row.text} · {statusLabels[row.kind]}{row.source && ` · ${row.source.label} · ${location(row)}`}
                </li>)}</ol>
              </> : <p role="status">Loading earlier revision.</p>}
            </section>}
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
            </div> : <p className="my-2 whitespace-pre-wrap break-words text-xs">Summary wording is unverified. {draft.summary}</p>}
            {draft.layout && <div className="my-2 space-y-1 text-xs">
              <p>{draft.layout.sections.map(section => section.heading).join(' · ')}</p>
              {draft.layout.sections.filter(section => section.wording).map(section => <p key={section.id} className="break-words">
                {section.heading} · Unverified wording · {section.wording}
              </p>)}
              {draft.layout.comparison && <p>Includes a source-value comparison table.</p>}
              {draft.format === 'xlsx' && <p>{draft.layout.columns.join(' · ')}</p>}
              {draft.layout.inline_references && <p>Includes references alongside findings.</p>}
              {draft.available_formats && <p>Export formats · {draft.available_formats.map(format => format.toUpperCase()).join(', ')}</p>}
            </div>}
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
                {draft.state !== 'stale' && draft.state !== 'cancelled' && !draft.pending_revision && <>
                  <button type="button" disabled={!!action || exporting} className="underline"
                    onClick={() => setEdit({id: draft.id, revision: draft.revision, title: draft.title, summary: draft.summary, wording: {}})}>Edit</button>
                  {draft.state === 'draft' && <button type="button" disabled={!!action} className="underline"
                    onClick={() => void change(draft, 'approve')}>Approve revision {draft.revision}</button>}
                  {draft.state === 'approved' && !exporting && <button type="button" disabled={!!action} className="underline"
                    onClick={() => void change(draft, 'export')}>Export locally</button>}
                  {draft.state === 'exported' && <a className="underline"
                    href={`${base}/${encodeURIComponent(draft.id)}/download?revision=${draft.revision}`}>Download {draft.format.toUpperCase()}</a>}
                </>}
                {onDraftTarget && !draft.pending_revision && draft.state !== 'cancelled' && <button type="button"
                  className="underline" disabled={!!action || exporting}
                  onClick={() => onDraftTarget({ id: draft.id, revision: draft.revision })}>Refine this draft</button>}
                {draft.state === 'stale' && <p role="status">A source or image changed. Select current sources to regenerate this draft in chat.</p>}
                {draft.workflow_steps && !draft.pending_revision && ['draft', 'approved'].includes(draft.state) &&
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
