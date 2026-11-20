const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function load(relative, imports) {
  const filename = path.resolve(__dirname, relative);
  const moduleObject = new Module(filename, module);
  moduleObject.require = name => imports[name];
  moduleObject._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, filename);
  return moduleObject.exports;
}

function nodes(node) {
  if (Array.isArray(node)) return node.flatMap(nodes);
  return node && typeof node === 'object' ? [node, ...nodes(node.props.children)] : [];
}

test('clarification offers only server choices and enables continuation at the requested count', () => {
  let selected = [];
  const jsx = (type, props) => ({ type, props });
  const { WorkflowClarification } = load('../src/components/chat/WorkflowClarification.tsx', {
    react: { useState: () => [selected, update => { selected = update(selected); }] },
    'react/jsx-runtime': { jsx, jsxs: jsx },
  });
  const messages = [];
  const props = {
    busy: false,
    clarification: { count: 2, question: 'Which documents?', options: [
      { id: 'owner-a', label: 'Alpha' }, { id: 'owner-b', label: 'Beta' }, { id: 'owner-c', label: 'Gamma' },
    ] },
    resume: ids => messages.push(ids),
    cancel: () => messages.push('cancelled'),
  };
  const controls = () => nodes(WorkflowClarification(props));
  const submit = () => controls().find(node => node.type === 'button' && node.props.children === 'Continue workflow');
  assert.equal(submit().props.disabled, true);
  for (const index of [0, 1]) {
    controls().filter(node => node.type === 'input')[index].props.onChange({ target: { checked: true } });
  }
  assert.equal(controls().filter(node => node.type === 'input')[2].props.disabled, true);
  assert.equal(submit().props.disabled, false);
  submit().props.onClick();
  controls().find(node => node.type === 'button' && node.props.children === 'Cancel workflow').props.onClick();
  assert.deepEqual(messages, [['owner-a', 'owner-b'], 'cancelled']);
});

test('continuation sends only the revision and typed choices while binding responses to the existing turn', async () => {
  let observed;
  const { streamLocalClarification } = load('../src/api/chat.ts', {
    './client': { streamSSE: async (...args) => { observed = args; } },
  });
  const turn = { session_id: 'existing-session', turn_id: 'existing-turn', attempt: 1 };
  await streamLocalClarification(turn, { revision: 2, selection: ['xlsx'], usesImage: true }, {});
  assert.equal(observed[0], '/api/chat/sessions/existing-session/turns/existing-turn/clarify');
  assert.deepEqual(observed[1], { revision: 2, selection: ['xlsx'] });
  assert.deepEqual(observed[4], { ...turn, image_id: 'attached' });
});

test('the paused file control restricts formats and never confirms a workflow by itself', () => {
  const jsx = (type, props) => ({ type, props });
  const { WorkflowClarification } = load('../src/components/chat/WorkflowClarification.tsx', {
    react: { useState: () => [[], () => {}] },
    'react/jsx-runtime': { jsx, jsxs: jsx },
  });
  const actions = [];
  const file = { name: 'maintenance.pdf' };
  const controls = (reason, other = {}) => nodes(WorkflowClarification({
    clarification: { reason, count: 1, options: [], question: 'Choose an attachment.' },
    upload: entry => actions.push(entry), cancelUpload: () => actions.push('cancelled'),
    retryUpload: () => actions.push('retried'), resume: () => actions.push('continued'), ...other,
  }));
  const choose = reason => controls(reason).find(node => node.props.type === 'file');
  const input = choose('sources');
  assert.equal(input.props.accept, '.txt,.md,.pdf,.docx,.xlsx');
  assert.equal(choose('image').props.accept, '.png,.jpg,.jpeg,.webp');
  assert.equal(choose('format'), undefined);
  input.props.onChange({ currentTarget: { files: [file], value: file.name } });
  controls('sources', { uploading: true }).find(node => node.props.children === 'Cancel upload').props.onClick();
  controls('sources', { uploadError: 'The file could not be read.' })
    .find(node => node.props.children === 'Retry upload').props.onClick();
  assert.deepEqual(actions, [file, 'cancelled', 'retried']);
});

test('attachment upload sends only the bytes and owned clarification coordinates', async () => {
  let observed;
  const { uploadClarificationAttachment } = load('../src/api/chat.ts', {
    './client': { apiRequest: async (...args) => { observed = args; return { turn: {} }; } },
  });
  const file = { name: 'notes & limits.pdf' };
  const signal = new AbortController().signal;
  await uploadClarificationAttachment({ session_id: 'current', turn_id: 'same-turn' }, 4, 'document', file, signal);
  const [route, init] = observed;
  const [endpoint, query] = route.split('?');
  assert.equal(endpoint, '/api/chat/sessions/current/turns/same-turn/clarify/attachment');
  assert.deepEqual(Object.fromEntries(new URLSearchParams(query)), { revision: '4', kind: 'document', filename: file.name });
  assert.equal(init.body, file);
  assert.equal(init.signal, signal);
  assert.equal(init.method, 'POST');
});
