import type { DocumentLocation, DocumentOutcome, DocumentWorkflowEvent } from '../../types/documentWorkflow';

const warningText = new Map([
  ['PDF_LAYOUT_UNAVAILABLE', 'Detailed page layout could not be extracted.'],
  ['PDF_NO_VERIFIED_TABLE_STRUCTURE', 'No verified table structure was found on this page.'],
  ['PDF_TABLE_GEOMETRY_LIMIT', 'The table layout exceeded extraction limits.'],
  ['PDF_TABLE_GEOMETRY_UNVERIFIED', 'The table layout could not be verified.'],
  ['PDF_OCR_UNAVAILABLE', 'Text from the scanned page is unavailable.'],
  ['PDF_EMPTY_PAGE', 'The page has no extractable evidence.'],
  ['DOCX_NESTED_TABLE_OMITTED', 'A nested table was omitted.'],
  ['DOCX_TABLE_GRID_UNVERIFIED', 'The table layout could not be verified.'],
  ['XLSX_FORMULA_TEXT_UNAVAILABLE', 'Formula text is unavailable.'],
]);
const operationText = new Map([['max', 'Maximum'], ['min', 'Minimum'], ['average', 'Average'], ['sum', 'Sum'],
  ['count', 'Valid numeric count'], ['above', 'Readings over the threshold'], ['difference', 'Difference']]);
const withheldReason = new Map([
  ['CANCELLED', 'Document analysis was cancelled.'],
  ['TIMEOUT', 'Document analysis exceeded its time budget.'],
  ['AUTHORIZATION_UNAVAILABLE', 'Document authorization is no longer available.'],
  ['REQUEST_CLOSED', 'The document request has closed.'],
]);

function coordinates(location: DocumentLocation, heading?: number): string {
  const items = [];
  if (location.page !== undefined) items.push(`Page ${location.page}`);
  if (location.paragraph !== undefined) items.push(`Paragraph ${location.paragraph}`);
  if (heading !== undefined) items.push(`Heading level ${heading}`);
  if (location.table !== undefined) items.push(`Table ${location.table}`);
  if (location.sheet !== undefined) items.push(`Worksheet ${location.sheet}`);
  if (location.cell !== undefined) items.push(`Cell ${location.cell}`);
  else {
    if (location.row !== undefined) items.push(`Row ${location.row}`);
    if (location.column !== undefined) items.push(`Column ${location.column}`);
  }
  return items.join(', ');
}

function workflowState(events: readonly DocumentWorkflowEvent[]) {
  if (events.length > 9 || events.some(event => event.task_id !== events[0]?.task_id)) return null;
  const plan = events[1];
  const steps = plan?.type === 'plan_created' ? plan.plan.steps : [];
  const last = events.at(-1);
  const terminal = last?.type === 'task_completed' || last?.type === 'task_failed' || last?.type === 'task_cancelled' ? last : null;
  if (steps.length < 2 || steps.length > 3) {
    if (events.length === 0 || (events.length === 1 && (last?.type === 'task_started' ||
        (terminal && terminal.type !== 'task_completed' && terminal.partial === null)))) return { steps: [], terminal };
    return null;
  }
  if (events[0]?.type !== 'task_started' || new Set(steps.map(step => step.id)).size !== steps.length) return null;
  const status = steps.map((): 'pending' | 'running' | 'completed' | 'failed' => 'pending');
  let index = 0;
  for (const event of events.slice(2, terminal ? -1 : undefined)) {
    if (event.type !== 'plan_step' || steps[index]?.id !== event.step_id ||
        (event.status === 'running' ? status[index] !== 'pending' : status[index] !== 'running')) return null;
    status[index] = event.status;
    if (event.status === 'completed') index++;
  }
  if (terminal?.type === 'task_completed' && index !== steps.length) return null;
  if (terminal?.type === 'task_failed' && terminal.partial && index === 0) return null;
  return { steps: steps.map((step, i) => ({ ...step, status: status[i] })), terminal };
}

function hasBoundEvidence(answer: DocumentOutcome): boolean {
  const sources = new Map(answer.sources.map(source => [source.source_id, source]));
  const referenced = new Set(answer.findings.flatMap(finding => finding.evidence.map(cite => cite.source_id)));
  return sources.size === answer.sources.length && referenced.size === sources.size && answer.findings.length > 0 &&
    answer.findings.every(finding => finding.evidence.length > 0 && finding.evidence.every(cite => {
      const location = cite.location;
      if (Object.values(location).some(value => typeof value === 'number' && (!Number.isSafeInteger(value) || value < 1))) return false;
      const table = location.table !== undefined && location.row !== undefined && location.column !== undefined;
      switch (sources.get(cite.source_id)?.format) {
        case 'pdf': return location.page !== undefined && location.paragraph === undefined &&
          location.sheet === undefined && location.cell === undefined && (location.table === undefined || table);
        case 'docx': return location.page === undefined && location.sheet === undefined && location.cell === undefined &&
          (table || (location.paragraph !== undefined && location.table === undefined));
        case 'xlsx': return !!location.sheet && !!location.cell && location.page === undefined && location.paragraph === undefined &&
          location.table === undefined;
        default: return false;
      }
    }) && (finding.kind !== 'comparison' || (finding.rows.length > 0 && finding.rows.every(row => {
      const [left, right] = row.operands.map(index => finding.evidence[index]);
      return !!left && !!right && left.source_id !== right.source_id;
    }))));
}

function Evidence({ answer }: { answer: DocumentOutcome }) {
  const sources = new Map(answer.sources.map(source => [source.source_id, source]));
  return <div className="space-y-3">
    <h3 className="font-semibold">Source evidence</h3>
    <ul className="list-disc space-y-1 pl-5 break-words">
      {answer.sources.map(source => <li key={source.source_id}>
        {source.filename} ({source.format.toUpperCase()})
        {source.warnings.length > 0 && <ul className="pl-4 text-slate-700">
          {source.warnings.map((warning, index) => <li key={index}>
            {coordinates(warning.location)}: {warningText.get(warning.code) ?? 'Some source evidence could not be extracted.'}
          </li>)}
        </ul>}
      </li>)}
    </ul>
    {answer.findings.map((finding, index) => <div className="space-y-2" key={index}>
      {finding.kind === 'calculation' && <>
        <p><strong>{operationText.get(finding.operation) ?? 'Calculation'}:</strong>{' '}
          {finding.approximate && 'approximately '}{finding.value}{finding.unit && ` ${finding.unit}`}</p>
        {(finding.excluded.missing + finding.excluded.nonnumeric + finding.excluded.formulas > 0) && <p>
          Excluded cells: {finding.excluded.missing} missing, {finding.excluded.nonnumeric} nonnumeric, {finding.excluded.formulas} formula.
        </p>}
      </>}
      {finding.kind === 'comparison' && <>
        <p><strong>Calculated differences</strong> (second reading minus first)</p>
        <ul className="list-disc pl-5 break-words">{finding.rows.slice(0, 8).map((row, i) => <li key={i}>
          {row.record}: {row.difference}{finding.unit && ` ${finding.unit}`} (evidence {row.operands[0] + 1} and {row.operands[1] + 1})
        </li>)}</ul>
        {finding.rows.length > 8 && <p>Showing 8 of {finding.rows.length} comparisons.</p>}
      </>}
      <ol className="list-decimal space-y-1 pl-5 break-words">
        {finding.evidence.slice(0, 36).map((cite, i) => <li key={i}>
          <span className="font-medium">{sources.get(cite.source_id)?.filename}, {coordinates(cite.location, cite.heading_level)}</span>
          <span className="block whitespace-pre-wrap">{cite.value}</span>
          {(cite.value_type !== 'text' || cite.number_format) && <span className="text-xs text-slate-600">
            {cite.value_type.replaceAll('_', ' ')}{cite.number_format && ` · Format ${cite.number_format}`}
          </span>}
        </li>)}
      </ol>
      {finding.evidence.length > 36 && <p>Showing 36 of {finding.evidence.length} citations.</p>}
    </div>)}
    <details><summary>Read the grounded synthesis</summary><p className="whitespace-pre-wrap break-words">{answer.text}</p></details>
  </div>;
}

/** Renders one authorized offline request. Keep events in memory for that request only. */
export function OfflineDocumentWorkflow({ events }: { events: readonly DocumentWorkflowEvent[] }) {
  const workflow = workflowState(events);
  if (!workflow) return <p role="alert">Document workflow status is unavailable.</p>;
  const { steps, terminal } = workflow;
  const failure = terminal?.type === 'task_failed' || terminal?.type === 'task_cancelled' ? terminal : null;
  const partial = failure?.type === 'task_failed' && !withheldReason.has(failure.error_code) &&
    failure.partial && hasBoundEvidence(failure.partial) ? failure.partial : null;
  if (terminal?.type === 'task_completed' && !hasBoundEvidence(terminal.answer)) return <p role="alert">Document evidence is unavailable.</p>;
  return <section aria-label="Offline document workflow" className="space-y-3 rounded-lg border border-slate-200 p-4 text-sm text-slate-800">
    <h2 className="font-semibold">Offline document workflow</h2>
    <p role="status">{failure ? (failure.type === 'task_cancelled' ? 'Cancelled.' : 'Workflow failed.') :
      terminal?.type === 'task_completed' ? 'Workflow completed.' : 'Awaiting document findings.'}</p>
    {steps.length > 0 && <ol className="list-decimal space-y-1 pl-5" aria-label="Workflow steps">
      {steps.map(step => <li key={step.id}>{step.description} · {step.status === 'completed' ? 'verified' : step.status}</li>)}
    </ol>}
    {failure && <p role="alert">{failure.operation && `${failure.operation}: `}{withheldReason.get(failure.error_code) ?? failure.error}</p>}
    {terminal?.type === 'task_completed' && <Evidence answer={terminal.answer} />}
    {partial && <><p className="font-medium">Earlier verified findings</p><Evidence answer={partial} /></>}
  </section>;
}
