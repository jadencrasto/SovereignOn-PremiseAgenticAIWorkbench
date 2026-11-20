import { useEffect, useRef, useState } from 'react';
import { listWorkflows, prepareWorkflowReuse, readWorkflow, workflowDownload,
  type ReusableWorkflow, type WorkflowDetail, type WorkflowSummary } from '../../api/workflows';

const states = {
  running: 'Running', waiting_clarification: 'Needs clarification', completed: 'Completed',
  failed: 'Failed', interrupted: 'Interrupted', cancelled: 'Cancelled',
};
const stepNames: Record<string, string> = {
  evidence: 'Evidence checked', retrieve: 'Source evidence retrieved', vision: 'Image analyzed',
  compare: 'Measurements compared', vision_unavailable: 'Image analysis unavailable', draft: 'Draft assembled',
};
const actionStyle = 'rounded px-1 text-blue-700 underline disabled:text-slate-400';
type OpenAction = 'open' | 'resume' | 'retry' | 'draft';
interface Props {
  refreshKey: number;
  onOpen: (item: WorkflowDetail, action: OpenAction, signal: AbortSignal, draftId?: string) => Promise<void>;
  onReuse: (template: ReusableWorkflow, signal: AbortSignal) => Promise<void>;
}

export function WorkflowLibrary({ refreshKey, onOpen, onReuse }: Props) {
  const [status, setStatus] = useState('');
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{ workflows: WorkflowSummary[]; next_offset: number | null }>();
  const [selected, setSelected] = useState<WorkflowSummary>();
  const [detail, setDetail] = useState<WorkflowDetail>();
  const [reload, setReload] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [choices, setChoices] = useState<string[]>();
  const operation = useRef<AbortController | undefined>(undefined);

  useEffect(() => {
    const refresh = () => setReload(value => value + 1);
    window.addEventListener('focus', refresh);
    return () => {
      window.removeEventListener('focus', refresh);
      operation.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (selected) return;
    const controller = new AbortController();
    setPage(undefined);
    setError('');
    void listWorkflows(status, query, offset, controller.signal).then(result => {
      if (!controller.signal.aborted) setPage(result);
    }).catch(() => {
      if (!controller.signal.aborted) setError('Workflow history could not be loaded. Refresh or sign in again.');
    });
    return () => controller.abort();
  }, [status, query, offset, reload, refreshKey, selected]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setDetail(undefined);
    setChoices(undefined);
    setError('');
    void readWorkflow(selected, controller.signal).then(result => {
      if (!controller.signal.aborted) setDetail(result);
    }).catch(() => {
      if (!controller.signal.aborted) setError('This workflow could not be opened. It may have been deleted or your session expired.');
    });
    return () => controller.abort();
  }, [selected, reload, refreshKey]);

  const act = async (action: OpenAction | 'reuse', draftId?: string) => {
    if (!detail || operation.current) return;
    const controller = new AbortController();
    operation.current = controller;
    setBusy(true);
    setError('');
    try {
      if (action === 'reuse') {
        const template = await prepareWorkflowReuse(detail, choices ?? [], controller.signal);
        if (!controller.signal.aborted) await onReuse(template, controller.signal);
      } else {
        const fresh = await readWorkflow(detail, controller.signal);
        if (controller.signal.aborted) return;
        setDetail(fresh);
        if ((action === 'resume' || action === 'retry') &&
          (fresh.attempt !== detail.attempt || fresh.plan_revision !== detail.plan_revision ||
            !(action === 'resume' ? fresh.can_resume : fresh.can_retry))) {
          setError('The workflow changed. Review its current state before continuing.');
        } else if (draftId && !fresh.drafts.some(draft => draft.id === draftId)) {
          setError('This draft is no longer available.');
        } else await onOpen(fresh, action, controller.signal, draftId);
      }
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'The workflow action could not be completed.');
    } finally {
      operation.current = undefined;
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  return (
    <div className="flex-1 min-h-0 space-y-3 overflow-y-auto p-3 text-sm text-slate-700">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        {selected && <button type="button" className={actionStyle} disabled={busy} onClick={() => setSelected(undefined)}>Back to workflows</button>}
        <button type="button" className={actionStyle} disabled={busy} onClick={() => setReload(value => value + 1)}>Refresh workflows</button>
      </div>
      {error && <p role="alert" className="text-red-700">{error}</p>}
      {selected ? detail ? (
        <section aria-label="Workflow details" className="space-y-3">
          <h3 className="break-words font-semibold">{detail.objective}</h3>
          <p>{states[detail.state]} · {detail.conversation_title}</p>
          {detail.steps.length > 0 && <ol aria-label="Recorded steps" className="list-inside list-decimal">
            {detail.steps.map(step => <li key={step}>{stepNames[step] || 'Recorded workflow step'}</li>)}
          </ol>}
          {detail.clarification && <p>{detail.clarification.question}</p>}
          {!detail.dependencies_current && <p role="status" className="text-amber-800">
            Evidence has changed or was removed. Historical results may be stale.
          </p>}
          {detail.sources.length > 0 && <ul aria-label="Selected sources">
            {detail.sources.map(source => <li key={source.id} className="break-words">
              {source.label} · Revision {source.revision}{source.current ? '' : ' · Changed or removed'}
            </li>)}
          </ul>}
          {detail.image && <p className="break-words">{detail.image.label}{detail.image.current ? '' : ' · Image unavailable'}</p>}
          {detail.last_error && <p>{detail.can_retry ? 'This attempt can be retried.' : 'Open the conversation for recovery details.'}</p>}
          {detail.drafts.map(draft => <article key={draft.id} className="space-y-1 rounded border border-slate-200 p-2">
            <h4 className="break-words font-medium">{draft.title}</h4>
            <p>{draft.format.toUpperCase()} · Revision {draft.revision} · {draft.stale ? 'Evidence stale' :
              draft.cancelled ? 'Cancelled' : draft.pending_revision ? 'Refinement interrupted' : draft.downloadable ? 'Export complete' :
              draft.approved ? 'Approved' : 'Awaiting review'}</p>
            {draft.revisions.length > 0 && <p>Earlier revisions {draft.revisions.join(', ')}</p>}
            <button type="button" className={actionStyle} disabled={busy} onClick={() => void act('draft', draft.id)}>
              Open draft and revisions
            </button>
            {draft.downloadable && <a className={actionStyle} href={workflowDownload(detail, draft)}>Download {draft.format.toUpperCase()}</a>}
          </article>)}
          <div className="flex flex-wrap gap-2">
            <button type="button" className={actionStyle} disabled={busy} onClick={() => void act('open')}>Open conversation</button>
            {detail.can_resume && <button type="button" className={actionStyle} disabled={busy} onClick={() => void act('resume')}>Continue clarification</button>}
            {detail.can_retry && <button type="button" className={actionStyle} disabled={busy} onClick={() => void act('retry')}>Retry workflow</button>}
            {detail.can_reuse && !choices && <button type="button" className={actionStyle} disabled={busy} onClick={() => setChoices([])}>Use as new task</button>}
          </div>
          {choices && <fieldset className="space-y-2 rounded border border-slate-200 p-2" disabled={busy}>
            <legend className="font-medium">Sources for the new task</legend>
            {detail.sources.filter(source => source.reusable).map(source => (
              <label key={source.id} className="flex gap-2 break-words">
                <input type="checkbox" checked={choices.includes(source.id)} onChange={event => setChoices(current =>
                  event.target.checked ? [...(current ?? []), source.id] : (current ?? []).filter(id => id !== source.id))} />
                {source.label} · Revision {source.revision}
              </label>
            ))}
            {detail.image && <p>Choose an image again if the new task needs one.</p>}
            <button type="button" className={actionStyle} disabled={detail.sources.length > 0 && choices.length === 0}
              onClick={() => void act('reuse')}>Review new task</button>
            <button type="button" className={actionStyle} onClick={() => setChoices(undefined)}>Cancel reuse</button>
          </fieldset>}
          {busy && <p role="status">Checking current workflow state…</p>}
        </section>
      ) : !error && <p role="status">Loading workflow…</p> : <>
        <label className="block">
          <span className="sr-only">Search workflow objectives</span>
          <input className="w-full rounded border border-slate-200 p-2" placeholder="Search workflow objectives"
            value={query} maxLength={120} onChange={event => { setQuery(event.target.value); setOffset(0); }} />
        </label>
        <label className="flex items-center gap-2">Status
          <select className="rounded border border-slate-200 p-1" value={status}
            onChange={event => { setStatus(event.target.value); setOffset(0); }}>
            <option value="">All statuses</option>
            {Object.entries(states).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </label>
        {!page ? !error && <p role="status">Loading workflows…</p> : <>
          {!page.workflows.length && <p>No workflows found.</p>}
          <ul className="space-y-2">
            {page.workflows.map(item => <li key={`${item.session_id}:${item.id}`} className="rounded border border-slate-200 p-2">
              <button type="button" className="block w-full text-left font-medium break-words hover:underline"
                onClick={() => setSelected(item)}>{item.objective}</button>
              <p className="text-xs">{states[item.state]} · {item.conversation_title} · <time dateTime={item.updated_at}>
                {new Date(item.updated_at).toLocaleString()}</time></p>
              {item.export_recorded ? <p className="text-xs">Export recorded</p> : item.has_draft && <p className="text-xs">Draft available for review</p>}
            </li>)}
          </ul>
          <div className="flex gap-2">
            {offset > 0 && <button type="button" className={actionStyle} onClick={() => setOffset(Math.max(0, offset - 12))}>Previous</button>}
            {page.next_offset !== null && <button type="button" className={actionStyle} onClick={() => setOffset(page.next_offset!)}>Next</button>}
          </div>
        </>}
      </>}
    </div>
  );
}
