const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const Module = require('module');
const ts = require('typescript');

function panel(request) {
  const slots = [];
  const effects = [];
  let cursor = 0;
  const react = {
    Fragment: 'fragment',
    useState: initial => {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }];
    },
    useRef: initial => slots[cursor++] ??= { current: initial },
    useEffect: (effect, dependencies) => {
      const index = cursor++;
      if (slots[index]?.some((value, offset) => value !== dependencies[offset]) || !slots[index]) effects.push(effect);
      slots[index] = dependencies;
    },
  };
  const file = path.resolve(__dirname, '../src/components/chat/LocalDraftPanel.tsx');
  const mod = new Module(file, module);
  const jsx = (type, props) => ({ type, props, children: props.children });
  mod.require = name => name === 'react' ? react : name === 'react/jsx-runtime'
    ? { jsx, jsxs: jsx, Fragment: react.Fragment } : { apiRequest: request };
  const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  mod._compile(compiled, file);
  const props = { sessionId: 'owned-session', refreshKey: '1', selected: [], messages: [], onSelection: () => {} };
  function render() {
    cursor = 0;
    return mod.exports.LocalDraftPanel(props);
  }
  async function reconcile() {
    render();
    effects.splice(0).forEach(effect => effect());
    await new Promise(setImmediate);
    return render();
  }
  return { props, render, reconcile };
}

function nodes(value) {
  if (Array.isArray(value)) return value.flatMap(nodes);
  return typeof value === 'object' && value !== null ? [value, ...nodes(value.children)] : [];
}

function text(value) {
  if (Array.isArray(value)) return value.map(text).join('');
  return typeof value === 'string' || typeof value === 'number' ? String(value) : text(value?.children ?? []);
}

const action = (tree, label) => nodes(tree).find(node => node.type === 'button' && text(node) === label);
const tick = () => new Promise(setImmediate);
const draft = {
  id: 'draft-id', revision: 1, format: 'xlsx', title: 'Pressure comparison', summary: 'Two approved sources.',
  state: 'draft', exporting: false, findings: [
    { topic: 'Working pressure', text: '8 bar', kind: 'source_value', coverage: 'supported',
      source: { label: 'register.xlsx', revision: 2, excerpt: 'P-501 8 bar' },
      field: { document_locator: { format: 'xlsx', sheet: 'Operating Register', cell: 'C4' } } },
    { topic: 'Image analysis', text: 'A blue surface.', kind: 'visual_interpretation',
      image: { filename: 'equipment.png', sha256: 'abc123', model: 'llava:7b' } },
  ],
};

test('draft panel restores citations and image provenance, then edits, approves and downloads the exact revision', async () => {
  let current = draft;
  const calls = [];
  const ui = panel(async (url, options) => {
    if (url.endsWith('/drafts')) return { drafts: [current] };
    const body = JSON.parse(options.body);
    calls.push({ url, method: options.method, body });
    if (options.method === 'PATCH') current = {
      ...current, revision: 2, findings: [{ ...draft.findings[0], text: body.wording[0], kind: 'user_edited' }],
    };
    if (url.endsWith('/approve')) current = { ...current, state: 'approved' };
    if (url.endsWith('/export')) current = { ...current, state: 'exported' };
    return current;
  });
  let tree = await ui.reconcile();
  for (const value of ['Operating Register!C4', 'P-501 8 bar', 'equipment.png', 'llava:7b', 'abc123']) {
    assert.ok(text(tree).includes(value));
  }
  assert.equal(action(tree, 'Export locally'), undefined);
  action(tree, 'Edit').props.onClick();
  tree = ui.render();
  const input = nodes(tree).find(node => node.type === 'input' && node.props.maxLength === 2000);
  input.props.onChange({ target: { value: 'Check the reading.' } });
  action(ui.render(), 'Save revision').props.onClick();
  await tick();
  tree = ui.render();
  assert.match(text(tree), /User-edited wording, unverified/);
  assert.match(text(tree), /Original reference/);
  action(tree, 'Approve revision 2').props.onClick();
  await tick();
  action(ui.render(), 'Export locally').props.onClick();
  await tick();
  const download = nodes(ui.render()).find(node => node.type === 'a' && text(node) === 'Download XLSX');
  assert.equal(download.props.href, '/api/chat/sessions/owned-session/drafts/draft-id/download?revision=2');
  assert.deepEqual(calls.map(call => [call.method, call.body.revision]), [['PATCH', 1], ['POST', 2], ['POST', 2]]);
});

test('draft panel cancels a pending export, shows the error and lets the owner retry', async () => {
  const approved = { ...draft, state: 'approved' };
  let interrupt;
  let exports = 0;
  const ui = panel(async url => {
    if (url.endsWith('/drafts')) return { drafts: [approved] };
    if (url.endsWith('/cancel-export')) {
      interrupt(new Error('The export was cancelled.'));
      return approved;
    }
    if (++exports === 1) return new Promise((_, reject) => { interrupt = reject; });
    return { ...approved, state: 'exported' };
  });
  action(await ui.reconcile(), 'Export locally').props.onClick();
  assert.match(text(ui.render()), /Generating local export/);
  action(ui.render(), 'Cancel export').props.onClick();
  await tick();
  assert.match(text(ui.render()), /The export was cancelled/);
  assert.equal(nodes(ui.render()).some(node => node.type === 'a'), false);
  action(ui.render(), 'Export locally').props.onClick();
  await tick();
  assert.ok(text(ui.render()).includes('Download XLSX'));
});
