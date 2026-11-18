const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const filename = path.resolve(__dirname, '../src/components/documents/OfflineDocumentWorkflow.tsx');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
} }).outputText;
const fixtureModule = new Module(filename, module);
fixtureModule.filename = filename;
fixtureModule.paths = Module._nodeModulePaths(path.dirname(filename));
fixtureModule._compile(compiled, filename);
const { OfflineDocumentWorkflow } = fixtureModule.exports;

const render = events => renderToStaticMarkup(React.createElement(OfflineDocumentWorkflow, { events }));
const event = (type, fields = {}) => ({ type, task_id: 'offline-fixture', ...fields });
const steps = [
  { id: 'retrieve', description: 'Find supporting evidence', status: 'pending' },
  { id: 'synthesize', description: 'Explain the verified findings', status: 'pending' },
];
const plan = () => [event('task_started', { status: 'planning' }),
  event('plan_created', { plan: { objective: 'Grounded document analysis', steps } })];
const progress = (step, status) => event('plan_step', { step_id: step.id, description: step.description, status });
const completed = answer => [...plan(), ...steps.flatMap(step => [progress(step, 'running'), progress(step, 'completed')]),
  event('task_completed', { answer })];

function passages() {
  return {
    text: 'Verified supporting passages.',
    sources: [
      { source_id: 'p', filename: 'pump<report>.pdf', format: 'pdf', warnings: [{ code: 'PDF_OCR_UNAVAILABLE', location: { page: 3 } }] },
      { source_id: 'd', filename: 'review.docx', format: 'docx', warnings: [] },
    ],
    findings: [
      { kind: 'passage', evidence: [{ source_id: 'p', location: { page: 2 }, value_type: 'text', value: '<script>Passage</script>' }] },
      { kind: 'passage', evidence: [{ source_id: 'd', location: { paragraph: 1, body: 1 }, heading_level: 2,
        value_type: 'text', value: 'Original heading' }] },
    ],
  };
}

test('offline presentation renders actual PDF and DOCX coordinates, warnings and plain text', () => {
  const markup = render(completed(passages()));
  assert.match(markup, /Offline document workflow/);
  assert.match(markup, /pump&lt;report&gt;\.pdf, Page 2/);
  assert.match(markup, /Page 3: Text from the scanned page is unavailable/);
  assert.match(markup, /review\.docx, Paragraph 1, Heading level 2/);
  assert.match(markup, /&lt;script&gt;Passage&lt;\/script&gt;/);
  assert.doesNotMatch(markup, /<script>|review\.docx, Page/);
});

test('numeric presentations preserve cell coordinates, exact text precision and exclusion counts', () => {
  const evidence = { source_id: 'x', location: { sheet: 'Sheet 1', row: 2, column: 2, cell: 'B2' },
    value_type: 'number', value: '70', number_format: '0.00' };
  const markup = render(completed({ text: 'Average supported by workbook cells.',
    sources: [{ source_id: 'x', filename: 'plant.xlsx', format: 'xlsx', warnings: [] }],
    findings: [{ kind: 'calculation', operation: 'average', value: '75.00', unit: '\u00b0C', approximate: false,
      excluded: { missing: 0, nonnumeric: 0, formulas: 1 }, evidence: [evidence] }],
  }));
  assert.match(markup, /plant\.xlsx, Worksheet Sheet 1, Cell B2/);
  assert.match(markup, /75\.00 \u00b0C/);
  assert.match(markup, /Format 0\.00/);
  assert.match(markup, /1 formula/);
});

test('comparison numbers are linked to each original table operand', () => {
  const markup = render(completed({ text: 'Calculated difference from two independent readings.',
    sources: [{ source_id: 'd', filename: 'inspection.docx', format: 'docx', warnings: [] },
      { source_id: 'x', filename: 'reading.xlsx', format: 'xlsx', warnings: [] }],
    findings: [{ kind: 'comparison', unit: 'm', rows: [{ record: 'Pump A', difference: '0.05', operands: [0, 1] }],
      evidence: [{ source_id: 'd', location: { table: 1, row: 2, column: 2 }, value_type: 'text', value: '0.4' },
        { source_id: 'x', location: { sheet: 'Sheet1', row: 2, column: 2, cell: 'B2' }, value_type: 'number', value: '45' }] }],
  }));
  assert.match(markup, /Pump A: 0\.05 m \(evidence 1 and 2\)/);
  assert.match(markup, /inspection\.docx, Table 1, Row 2, Column 2/);
  assert.match(markup, /reading\.xlsx, Worksheet Sheet1, Cell B2/);
});

test('pending workflow displays ordered step progress without an outcome', () => {
  const markup = render([...plan(), progress(steps[0], 'running')]);
  assert.match(markup, /Find supporting evidence.*running/);
  assert.match(markup, /Explain the verified findings.*pending/);
  assert.doesNotMatch(markup, /Workflow completed|Source evidence/);
});

const failed = (error_code, partial, type = 'task_failed') => [...plan(), progress(steps[0], 'running'), progress(steps[0], 'completed'),
  progress(steps[1], 'running'), progress(steps[1], 'failed'), event(type, {
    error_code, partial, error: 'The worksheet could not be found.', step_id: 'synthesize', operation: 'Explain the verified findings',
  })];
test('failed dependent step displays earlier evidence without declaring success', () => {
  const markup = render(failed('WORKSHEET_UNAVAILABLE', passages()));
  assert.match(markup, /Workflow failed/);
  assert.match(markup, /Earlier verified findings/);
  assert.match(markup, /The worksheet could not be found/);
  assert.doesNotMatch(markup, /Workflow completed/);
});

for (const code of ['AUTHORIZATION_UNAVAILABLE', 'TIMEOUT', 'CANCELLED']) {
  test(`${code} withholds protected partial evidence and unsafe error text`, () => {
    const events = failed(code, passages(), code === 'CANCELLED' ? 'task_cancelled' : 'task_failed');
    events.at(-1).error = 'SECRET ERROR';
    const markup = render(events);
    assert.doesNotMatch(markup, /SECRET ERROR|review\.docx|Earlier verified findings|Workflow completed/);
    assert.match(markup, code === 'CANCELLED' ? /Cancelled/ : /Workflow failed/);
  });
}

test('an unbound reference or malformed format coordinate suppresses the outcome', () => {
  const answer = passages();
  answer.findings[0].evidence[0].source_id = 'another-request';
  assert.match(render(completed(answer)), /Document evidence is unavailable/);
  answer.findings[0].evidence[0].source_id = 'p';
  answer.findings[1].evidence[0].location.page = 77;
  const markup = render(completed(answer));
  assert.match(markup, /Document evidence is unavailable/);
  assert.doesNotMatch(markup, /review\.docx|Workflow completed/);
});

test('results from separate requests or a premature terminal event cannot complete this workflow', () => {
  const events = completed(passages());
  events.at(-1).task_id = 'another-request';
  assert.match(render(events), /Document workflow status is unavailable/);
  assert.match(render([...plan(), event('task_completed', { answer: passages() })]), /Document workflow status is unavailable/);
});
