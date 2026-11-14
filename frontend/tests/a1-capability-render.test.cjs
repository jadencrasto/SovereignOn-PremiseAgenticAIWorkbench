const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

function loadTs(relative, mocks = {}) {
  const filename = path.resolve(__dirname, relative);
  const source = fs.readFileSync(filename, 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true,
  }}).outputText;
  const mod = new Module(filename, module);
  mod.filename = filename;
  mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = mod.require.bind(mod);
  mod.require = (name) => Object.hasOwn(mocks, name) ? mocks[name] : original(name);
  mod._compile(output, filename);
  return mod.exports;
}

const helpers = loadTs('../src/api/capabilities.ts');
let state;
const { SystemHealth } = loadTs('../src/components/health/SystemHealth.tsx', {
  '../../api/capabilities': helpers,
  '../../context/WorkbenchContext': { useWorkbench: () => state },
});
function render(report) {
  state = { capabilities: report, isCheckingHealth: false, refreshCapabilities: () => {} };
  return renderToStaticMarkup(React.createElement(SystemHealth));
}
function fixture() {
  const capabilities = Object.fromEntries(Object.keys(helpers.capabilityLabels).map(key =>
    [key, { state: 'unknown', reason: 'model_check_required' }]));
  capabilities.backend = { state: 'available', reason: 'startup_complete' };
  capabilities.document_rag = { state: 'blocked', reason: 'protected_document_storage_unverified' };
  const model = { id: 'ollama/qwen:7b', state: 'unknown', reason: 'model_check_required' };
  return { schema_version: 1, environment: 'development', deployment_verified: false,
    capabilities, models: { default_chat: model }, installed_models: [] };
}
test('unavailable backend renders actionable status', () => {
  const html = render(null);
  assert.match(html, /unavailable/i);
  assert.match(html, /readiness endpoint did not respond/i);
});
test('configured model, unknown state and blocked feature are rendered', () => {
  const html = render(fixture());
  assert.match(html, /ollama\/qwen:7b/);
  assert.match(html, /unknown/i);
  assert.match(html, /blocked/i);
  assert.match(html, /Protected document storage and index integrity are unverified/);
});
test('render recovers after a failed backend response', () => {
  assert.match(render(null), /readiness endpoint did not respond/i);
  assert.match(render(fixture()), /Backend startup completed/);
});
test('an installed alternate model cannot override blocked, unavailable or unknown chat', () => {
  const report = fixture();
  report.capabilities.ollama = { state: 'available', reason: 'inventory_observed' };
  report.installed_models = ['alternate:7b'];
  for (const state of ['blocked', 'unavailable', 'unknown']) {
    report.capabilities.chat = { state, reason: 'component_not_initialized' };
    assert.equal(helpers.chatDisabled(report, false, 'ollama/alternate:7b'), true);
  }
  report.capabilities.chat = { state: 'available', reason: 'prerequisites_observed' };
  assert.equal(helpers.chatDisabled(report, false, 'ollama/alternate:7b'), false);
  assert.equal(helpers.chatDisabled(report, false, 'ollama/missing:7b'), true);
});
test('alternate inventory accepts implicit latest tags as the backend does', () => {
  const report = fixture();
  report.capabilities.ollama = { state: 'available', reason: 'inventory_observed' };
  report.capabilities.chat = { state: 'available', reason: 'prerequisites_observed' };
  report.installed_models = ['alternate:latest', 'vendor/other'];
  assert.equal(helpers.chatDisabled(report, false, 'ollama/alternate'), false);
  assert.equal(helpers.chatDisabled(report, false, 'ollama/vendor/other:latest'), false);
  assert.equal(helpers.chatDisabled(report, false, 'ollama/alternate:other'), true);
});
