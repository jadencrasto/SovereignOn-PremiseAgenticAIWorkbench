const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function fixture(t, initial, { confirm = true } = {}) {
  const previousWindow = global.window;
  global.window = { confirm: () => confirm };
  t.after(() => { global.window = previousWindow; });
  let cursor = 0;
  let mount;
  let server = initial;
  let failWrite = false;
  const slots = [];
  const sent = [];
  const react = {
    useState(value) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = value;
      return [slots[index], next => { slots[index] = typeof next === 'function' ? next(slots[index]) : next; }];
    },
    useEffect(effect) { mount = effect; },
  };
  const element = (type, props) => ({ type, props });
  const filename = path.resolve(__dirname, '../src/components/chat/LocalHistorySettings.tsx');
  const component = new Module(filename, module);
  component.require = name => {
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return { jsx: element, jsxs: element, Fragment: 'fragment' };
    if (name === '../../api/client') return { apiRequest: async (url, options) => {
      assert.equal(url, '/api/chat/local-retention');
      if (options.method === 'PUT') {
        sent.push(JSON.parse(options.body));
        if (failWrite) throw new Error('Unavailable');
        server = { ...server, next_mode: sent.at(-1).mode };
      }
      return server;
    } };
    throw new Error('Unexpected module: ' + name);
  };
  component._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, filename);
  const render = () => {
    cursor = 0;
    return component.exports.LocalHistorySettings({ currentMode: initial.mode });
  };
  return {
    render, sent, setFailure: () => { failWrite = true; },
    async load() { render(); const cleanup = mount(); t.after(cleanup); await new Promise(setImmediate); },
  };
}

function select(node) {
  if (Array.isArray(node)) return node.map(select).find(Boolean);
  if (!node || typeof node !== 'object') return;
  return node.type === 'select' ? node.props : select(node.props?.children);
}

function textOf(node) {
  if (Array.isArray(node)) return node.map(textOf).join(' ');
  if (node && typeof node === 'object') return textOf(node.props?.children);
  return typeof node === 'string' ? node : '';
}

test('durable choice is confirmed and applies at restart while active mode stays disposable', async t => {
  const ui = fixture(t, { mode: 'disposable', next_mode: 'disposable', launch_override: false });
  await ui.load();
  select(ui.render()).onChange({ currentTarget: { value: 'durable' } });
  await new Promise(setImmediate);
  assert.deepEqual(ui.sent, [{ mode: 'durable' }]);
  assert.equal(select(ui.render()).value, 'durable');
  assert.match(textOf(ui.render()), /History mode.*Current run only.*Restart to apply/);
});

test('cancelling next-run change leaves retained mode in place', async t => {
  const ui = fixture(t, { mode: 'durable', next_mode: 'durable', launch_override: false }, { confirm: false });
  await ui.load();
  select(ui.render()).onChange({ currentTarget: { value: 'disposable' } });
  assert.equal(select(ui.render()).value, 'durable');
  assert.deepEqual(ui.sent, []);
});

test('failed request does not display an unconfirmed retained-history setting', async t => {
  const ui = fixture(t, { mode: 'disposable', next_mode: 'disposable', launch_override: false });
  await ui.load();
  ui.setFailure();
  select(ui.render()).onChange({ currentTarget: { value: 'durable' } });
  await new Promise(setImmediate);
  assert.equal(select(ui.render()).value, 'disposable');
  assert.match(textOf(ui.render()), /could not confirm/);
  assert.doesNotMatch(textOf(ui.render()), /Restart to apply/);
});

test('operator launch override displays authoritative mode with no editable preference', async t => {
  const ui = fixture(t, { mode: 'durable', next_mode: 'durable', launch_override: true });
  await ui.load();
  assert.equal(select(ui.render()), undefined);
  assert.match(textOf(ui.render()), /History mode.*Saved across restarts.*Controlled by the launch setting/);
});
