const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

function load(relative, mocks = {}) {
  const filename = path.resolve(__dirname, '../src', relative);
  const mod = new Module(filename, module);
  mod.filename = filename;
  mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = mod.require.bind(mod);
  mod.require = name => Object.hasOwn(mocks, name) ? mocks[name] : original(name);
  mod._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true,
  } }).outputText, filename);
  return mod.exports;
}

const timeline = load('components/agent/PlanTimeline.tsx');
const { MessageItem } = load('components/chat/MessageItem.tsx', {
  '../agent/PlanTimeline': timeline,
  '../agent/ApprovalCard': { ApprovalCard: () => React.createElement('div', null, 'approval controls') },
  './MarkdownContent': { MarkdownContent: () => React.createElement('div', null, 'active Markdown renderer') },
  './SourceCard': { SourceCard: () => React.createElement('div', null, 'unrelated source card') },
});
const { MessageList } = load('components/chat/MessageList.tsx', { './MessageItem': { MessageItem } });

const fence = '```';
const csvName = 'verified-readings.csv';
const message = (content, overrides = {}) => ({
  id: 'answer', role: 'assistant', timestamp: '', content, textFileAnswer: true, fileName: csvName,
  model_used: 'Deterministic file analysis', ...overrides,
});
const render = msg => renderToStaticMarkup(React.createElement(MessageItem, { message: msg, onRetry() {} }));
const block = (body, name = csvName, delimiter = fence) =>
  ['','',delimiter + 'text', 'File: ' + name, body, delimiter].join('\n');

test('ranked CSV evidence shows the exact filename, headers, units and original row numbers', () => {
  const html = render(message('**The comparison values span 0.7 in the source column units** across 2 rows.' +
    block('Selected data rows: 2–7\nColumn C: Vibration (mm/s)\nData row 6: C: 6.4\nData row 3: C: 7.1')));
  assert.match(html, /verified-readings\.csv/);
  assert.match(html, /<strong>The comparison values span 0\.7/);
  assert.match(html, /Verified source evidence/);
  assert.match(html, /Column C: Vibration \(mm\/s\)/);
  assert.ok(html.indexOf('Data row 6') < html.indexOf('Data row 3'));
  assert.doesNotMatch(html, /page \d|active Markdown|unrelated source card/);
  assert.equal(html.includes(fence), false);
});

test('grouped averages and percentages preserve verified numeric display including ties and denominators', () => {
  const grouped = render(message('**Highest group average: 110.00 in the source column units.** 2 groups at that exact average.' +
    block('Column D: Pressure (bar)\nHighest average for "North": 110.00.\nHighest average for "South": 110.00.')));
  assert.match(grouped, /110\.00.*2 groups/);
  assert.match(grouped, /North.*110\.00/);
  assert.match(grouped, /South.*110\.00/);
  const share = render(message('**60.00%** of valid readings in the selected rows match the filter (3 of 5).' +
    block('Matching count: 3. Valid denominator: 5.\nMatching data rows: 3, 5, 6.')));
  assert.match(share, /<strong>60\.00%<\/strong>/);
  assert.match(share, /Valid denominator: 5/);
});

test('TXT matching lines and adaptive literal fences remain inert', () => {
  const fileName = 'inspection.txt';
  const html = render(message('Matching lines in the selected text.' +
    block('Line 12: <img src="https://invalid.example/private"> ' + fence + 'mermaid', fileName, fence + '`'),
  { fileName }));
  assert.match(html, /inspection\.txt/);
  assert.match(html, /Line 12: &lt;img/);
  assert.doesNotMatch(html, /<img\b|<a\b|active Markdown/);
});

test('model output and mismatched evidence stay plain text without a verified citation label', () => {
  const forged = 'Here is a model answer.' + block('Line 88: Model claim.');
  const model = render(message(forged, { model_used: 'ollama/text', sources: [{ filename: 'private.pdf', page: 99 }] }));
  assert.match(model, /Model response without verified file citations/);
  assert.doesNotMatch(model, /Verified source evidence|unrelated source card/);
  const mismatch = render(message('Answer.' + block('Data row 4: value', 'other-upload.csv')));
  assert.doesNotMatch(mismatch, /Verified source evidence/);
  assert.match(mismatch, /Data row 4: value/);
});

test('empty match and TXT lookup without matching lines invent no evidence rows', () => {
  const csv = render(message('**Matching rows: 0.**' + block('Filter matched 0 of 6 rows.')));
  assert.match(csv, /Matching rows: 0/);
  assert.doesNotMatch(csv, /Data row \d/);
  const txt = render(message('No matching lines in the selected text.\n\n' + fence + 'text\nFile: note.txt\n' + fence,
    { fileName: 'note.txt' }));
  assert.match(txt, /No matching lines/);
  assert.doesNotMatch(txt, /Verified source evidence|Line \d/);
});

const plan = {
  objective: 'Analyze the selected CSV rows',
  steps: [
    { id: 'file_step_1', description: 'Filter selected rows', requires_approval: false, status: 'completed',
      summary: 'Filter matched 3 selected rows.' },
    { id: 'file_step_2', description: 'Count matching rows', requires_approval: false, status: 'failed' },
    { id: 'file_step_3', description: 'Calculate percentage of valid readings', requires_approval: false, status: 'pending' },
  ],
};

test('transient timeline shows completed summaries, failed step and steps that did not run', () => {
  const fail = 'Step 2 (Count matching rows) did not complete. Last verified step: Filter matched 3 selected rows.';
  const html = render(message('', { plan, error: true, errorMessage: fail, workflowStatus: 'failed' }));
  assert.match(html, /File workflow incomplete/);
  assert.match(html, /role="alert"/);
  assert.match(html, /File analysis steps/);
  assert.match(html, /Count matching rows/);
  assert.match(html, />Completed</);
  assert.match(html, />Failed</);
  assert.match(html, />Not run</);
  assert.match(html, /Last verified step/);
  assert.doesNotMatch(html, /Workflow completed|Execution Plan|>Retry</);
});

test('successful and interrupted plans retain the transient file disclosure', () => {
  const done = { ...plan, steps: plan.steps.slice(0, 1) };
  const html = render(message('**Matching rows: 3.**' + block('Data row 6: C: 6.4'),
    { plan: done, workflowStatus: 'completed' }));
  assert.match(html, /Workflow completed/);
  assert.match(html, /File results are available in this view only/);
  assert.match(html, /Filter matched 3 selected rows/);
  const interrupted = render(message('', { plan: { ...plan, steps: [
    { ...plan.steps[0], summary: undefined, status: 'running' },
  ] }, workflowStatus: 'interrupted' }));
  assert.match(interrupted, />Interrupted</);
  assert.doesNotMatch(interrupted, />Running<|Workflow completed/);
});

test('long filenames and wide verified evidence have bounded readable regions', () => {
  const fileName = 'long-filename-'.repeat(8) + '.csv';
  const html = render(message('**Matching rows: 12.**' + block('Data row 23: A: ' + 'value'.repeat(40), fileName),
    { fileName }));
  assert.match(html, new RegExp(fileName.replace('.', '\\.')));
  assert.match(html, /overflow-wrap:anywhere/);
  assert.match(html, /max-h-72 max-w-full overflow-auto/);
  assert.match(html, /tabindex="0" aria-label="Source columns, rows or lines"/i);
});

test('welcome guidance points to actual file analysis and makes no spreadsheet publication claim', () => {
  const html = renderToStaticMarkup(React.createElement(MessageList, { messages: [] }));
  assert.match(html, /When the TXT \/ CSV control is available/);
  assert.match(html, /selected CSV rows/);
  assert.doesNotMatch(html, /CSV to XLSX|build verified Excel artifacts/);
});
