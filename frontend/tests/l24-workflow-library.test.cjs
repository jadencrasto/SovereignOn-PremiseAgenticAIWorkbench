const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const item = {
  id: 'workflow-1', session_id: 'conversation-1', conversation_title: 'Machine readings',
  objective: 'Compare the two maintenance reports.', state: 'completed', updated_at: '2026-11-19T12:00:00Z',
  has_draft: true, export_recorded: true,
};
const draft = { id: 'draft-1', title: 'Maintenance report', format: 'docx', revision: 3, revisions: [1, 2],
  approved: true, downloadable: true };
const source = { id: 'source-1', label: 'Maintenance A', revision: 1, current: true, reusable: true };
const completed = { ...item, attempt: 1, plan_revision: null, steps: ['retrieve', 'draft'], drafts: [draft],
  sources: [source], dependencies_current: true, can_reuse: true };

const tick = () => new Promise(setImmediate);
const children = value => Array.isArray(value) ? value.flatMap(children) :
  value && typeof value === 'object' ? [value, ...children(value.props.children)] : [];
const label = value => Array.isArray(value) ? value.map(label).join('') :
  value && typeof value === 'object' ? label(value.props.children) :
  ['string', 'number'].includes(typeof value) ? String(value) : '';
const button = (tree, text) => children(tree).find(node => node.type === 'button' && label(node) === text);
const click = (tree, text) => button(tree, text).props.onClick();

function mount(api, callbacks = {}) {
  const slots = [];
  const effects = [];
  const listeners = new Map();
  const window = {
    addEventListener(name, callback) { listeners.set(name, callback); },
    removeEventListener(name) { listeners.delete(name); },
  };
  let cursor = 0;
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }];
    },
    useRef(initial) { return slots[cursor++] ??= { current: initial }; },
    useEffect(effect, dependencies) {
      const index = cursor++;
      const previous = slots[index];
      if (!previous || dependencies.some((value, i) => value !== previous.deps[i])) {
        effects.push(() => {
          previous?.cleanup?.();
          slots[index] = { deps: dependencies, cleanup: effect() };
        });
      }
    },
  };
  const file = path.resolve(__dirname, '../src/components/chat/WorkflowLibrary.tsx');
  const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const mod = new Module(file, module);
  const jsx = (type, props) => ({ type, props });
  const defaults = {
    listWorkflows: async () => ({ workflows: [item], next_offset: null }),
    readWorkflow: async () => completed,
    workflowDownload: (_, row) => `/drafts/${row.id}/download?revision=${row.revision}`,
  };
  mod.require = name => name === 'react' ? react : name === 'react/jsx-runtime'
    ? { jsx, jsxs: jsx, Fragment: 'fragment' } : { ...defaults, ...api };
  new Function('module', 'exports', 'require', 'window', compiled)(mod, mod.exports, mod.require, window);
  const props = { refreshKey: 0, ...callbacks };
  const render = () => {
    cursor = 0;
    return mod.exports.WorkflowLibrary(props);
  };
  const reconcile = async () => {
    render();
    effects.splice(0).forEach(effect => effect());
    await tick();
    return render();
  };
  return { reconcile, render, focus: () => listeners.get('focus')() };
}

test('search and status changes reset pagination, suppress late results, and show read errors', async () => {
  const pending = [];
  const view = mount({
    listWorkflows(status, query, offset, signal) {
      return new Promise((resolve, reject) => pending.push({ status, query, offset, signal, resolve, reject }));
    },
  });
  let tree = await view.reconcile();
  assert.match(label(tree), /Loading workflows/);
  pending[0].resolve({ workflows: [item], next_offset: 12 });
  await tick();
  click(view.render(), 'Next');
  tree = await view.reconcile();
  assert.equal(pending[1].offset, 12);
  children(tree).find(node => node.type === 'input').props.onChange({ target: { value: 'pressure' } });
  tree = await view.reconcile();
  children(tree).find(node => node.type === 'select').props.onChange({ target: { value: 'failed' } });
  await view.reconcile();
  assert.deepEqual([pending[3].status, pending[3].query, pending[3].offset], ['failed', 'pressure', 0]);
  assert.ok(pending[1].signal.aborted && pending[2].signal.aborted);
  pending[1].resolve({ workflows: [item], next_offset: null });
  pending[3].reject(new Error('Unavailable'));
  await tick();
  tree = view.render();
  assert.doesNotMatch(label(tree), /Compare the two maintenance reports/);
  assert.ok(children(tree).find(node => node.props.role === 'alert'));
});

test('reopening exposes exact draft revision access and refresh withdraws stale downloads', async () => {
  let detail = completed;
  const actions = [];
  const view = mount({ readWorkflow: async () => detail }, { onOpen: async (...args) => actions.push(args) });
  click(await view.reconcile(), item.objective);
  let tree = await view.reconcile();
  assert.match(label(tree), /Earlier revisions 1, 2/);
  assert.equal(children(tree).find(node => node.type === 'a').props.href, '/drafts/draft-1/download?revision=3');
  assert.equal(actions.length, 0);
  click(tree, 'Open draft and revisions');
  await tick();
  assert.equal(actions[0][1], 'draft');
  assert.equal(actions[0][0].id, item.id);
  assert.equal(actions[0][3], draft.id);

  detail = { ...completed, dependencies_current: false, drafts: [{ ...draft, stale: true, downloadable: false }] };
  view.focus();
  tree = await view.reconcile();
  assert.match(label(tree), /Evidence has changed or was removed/);
  assert.equal(children(tree).some(node => node.type === 'a'), false);
  click(tree, 'Open conversation');
  await tick();
  assert.equal(actions[1][1], 'open');
});

test('continuation and retry recheck server state before issuing navigation or execution', async () => {
  const opens = [];
  let detail = { ...completed, state: 'waiting_clarification', plan_revision: 1, drafts: [],
    can_resume: true, clarification: { question: 'Which sources?' } };
  const view = mount({ readWorkflow: async () => detail }, { onOpen: async (...args) => opens.push(args) });
  click(await view.reconcile(), item.objective);
  let tree = await view.reconcile();
  assert.match(label(tree), /Which sources/);
  assert.equal(opens.length, 0);
  click(tree, 'Continue clarification');
  await tick();
  assert.equal(opens[0][1], 'resume');
  detail = { ...detail, state: 'interrupted', can_resume: false, can_retry: true, clarification: null };
  click(view.render(), 'Continue clarification');
  await tick();
  tree = view.render();
  assert.match(label(tree), /workflow changed/);
  assert.equal(opens.length, 1);
  click(tree, 'Retry workflow');
  await tick();
  assert.equal(opens[1][1], 'retry');
});

test('reuse prepares a separate task for review with explicitly selected sources', async () => {
  const inputs = [];
  const reviews = [];
  const template = { turn_id: 'new-turn', message: item.objective, knowledge_source_ids: [source.id] };
  const view = mount({
    readWorkflow: async () => ({ ...completed, sources: [source, { ...source, id: 'scoped', reusable: false }] }),
    prepareWorkflowReuse: async (_, selection) => { inputs.push(selection); return template; },
  }, { onReuse: async value => reviews.push(value) });
  click(await view.reconcile(), item.objective);
  click(await view.reconcile(), 'Use as new task');
  let tree = view.render();
  assert.ok(button(tree, 'Review new task').props.disabled);
  const sources = children(tree).filter(node => node.type === 'input');
  assert.equal(sources.length, 1);
  sources[0].props.onChange({ target: { checked: true } });
  tree = view.render();
  click(tree, 'Review new task');
  await tick();
  assert.deepEqual(inputs, [[source.id]]);
  assert.deepEqual(reviews, [template]);
});
