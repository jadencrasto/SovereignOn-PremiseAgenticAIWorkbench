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

test('only the explicit local profile permits chat from a local inventory observation', () => {
  const report = fixture();
  const model = report.models.default_chat.id;
  report.installed_models = ['qwen:7b'];
  report.capabilities.chat = { state: 'available', reason: 'model_listed' };
  report.selected_chat_model = { id: model, state: 'available', reason: 'model_listed', identity_evidence: 'provider_digest' };
  assert.equal(helpers.chatDisabled(report, false, model), true);
  report.run_profile = 'local_dev';
  assert.equal(helpers.chatDisabled(report, false, model), false);
  assert.equal(helpers.chatDisabled(report, true, model), true);
  report.selected_chat_model.identity_evidence = 'unavailable';
  assert.equal(helpers.chatDisabled(report, false, model), true);
});
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
test('offline document status, gated routes, and unverified browser integration are distinct', () => {
  const report = visionFixture();
  report.text_file_understanding = true;
  report.document_intelligence = { formats: ['PDF', 'DOCX', 'XLSX'], extraction: 'offline_verified',
    reasoning: 'offline_verified', planning: 'offline_verified', direct_chat: 'blocked',
    artifact_publication: 'blocked', external_browser: 'unverified' };
  const markup = render(helpers.parseCapabilityReport(report));
  assert.match(markup, /Request-scoped analysis is enabled for authenticated local chat with a ready model/);
  assert.match(markup, /Extraction, source-grounded reasoning and agent planning verified offline. Direct document chat is blocked/);
  assert.match(markup, /Protected artifact publication<\/dt>.*Blocked/);
  assert.match(markup, /External browser integration<\/dt>.*Unverified/);
  report.document_intelligence.direct_chat = 'available';
  assert.throws(() => helpers.parseCapabilityReport(report), /Invalid capability response/);
  delete report.document_intelligence;
  assert.match(render(helpers.parseCapabilityReport(report)), /Offline document verification status unknown/);
});
test('model scanner reports the vision check separately from image chat readiness', () => {
  const report = fixture();
  const vision = 'ollama/vision:latest';
  report.capabilities.multimodal = { state: 'unknown', reason: 'inference_not_checked' };
  report.vision_models = [vision];
  report.selected_vision_model = { id: vision, state: 'available', reason: 'inference_verified' };
  state = { capabilities: report, availableModels: [], defaultModel: 'ollama/qwen:7b',
    selectedVisionModel: vision, isBackendConnected: true, isCheckingHealth: false };
  const { ModelScanner } = loadTs('../src/components/models/ModelScanner.tsx', {
    '../../api/capabilities': helpers,
    '../../context/WorkbenchContext': { useWorkbench: () => state },
  });
  const html = renderToStaticMarkup(React.createElement(ModelScanner));
  assert.match(html, /completed a recent inference check/);
  assert.doesNotMatch(html, /not been checked for chat inference/);
});
test('an installed alternate model cannot override blocked, unavailable or unknown chat', () => {
  const report = fixture();
  report.capabilities.ollama = { state: 'available', reason: 'inventory_observed' };
  report.installed_models = ['alternate:7b'];
  for (const state of ['blocked', 'unavailable', 'unknown']) {
    report.capabilities.chat = { state, reason: 'component_not_initialized' };
    assert.equal(helpers.chatDisabled(report, false, 'ollama/alternate:7b'), true);
  }
  report.capabilities.chat = { state: 'available', reason: 'inference_verified' };
  assert.equal(helpers.chatDisabled(report, false, 'ollama/alternate:7b'), true);
  const future = Date.now() / 1000 + 60;
  report.selected_chat_model = { id: 'ollama/qwen:7b', state: 'available', expires_at: future };
  assert.equal(helpers.chatDisabled(report, false, 'ollama/alternate:7b'), true);
  report.selected_chat_model.id = 'ollama/alternate:7b';
  for (const expiry of [undefined, Date.now() / 1000 - 1, NaN, Infinity]) {
    report.selected_chat_model.expires_at = expiry;
    assert.equal(helpers.chatDisabled(report, false, 'ollama/alternate:7b'), true);
  }
  report.selected_chat_model.expires_at = future;
  assert.equal(helpers.chatDisabled(report, false, 'ollama/alternate:7b'), false);
  report.installed_models = [];
  assert.equal(helpers.chatDisabled(report, false, 'ollama/alternate:7b'), true);
});
test('alternate inventory accepts implicit latest tags as the backend does', () => {
  const report = fixture();
  report.capabilities.ollama = { state: 'available', reason: 'inventory_observed' };
  report.installed_models = ['alternate:latest', 'vendor/other'];
  assert.equal(helpers.modelInInventory(report, 'ollama/alternate'), true);
  assert.equal(helpers.modelInInventory(report, 'ollama/vendor/other:latest'), true);
  assert.equal(helpers.modelInInventory(report, 'ollama/alternate:other'), false);
});

test('readiness conflict preserves inventory and reason without granting chat readiness', async (t) => {
  const client = loadTs('../src/api/client.ts');
  const health = loadTs('../src/api/health.ts', { './client': client, './capabilities': helpers });
  const originalFetch = global.fetch;
  t.after(() => { global.fetch = originalFetch; });
  const report = fixture();
  const model = 'ollama/qwen:7b';
  for (const role of ['image_chat', 'vision', 'embedding']) report.models[role] = report.models.default_chat;
  report.capabilities.ollama = { state: 'available', reason: 'inventory_observed' };
  report.installed_models = ['qwen:7b'];
  report.chat_models = [model];
  report.selected_chat_model = { id: model, state: 'unknown', reason: 'inference_check_in_progress' };
  global.fetch = async () => Response.json(report, { status: 409 });

  for (const check of [() => health.checkModelInference(model), () => health.fetchCapabilities(true, model)]) {
    const received = await check();
    assert.deepEqual(received.chat_models, [model]);
    assert.equal(received.selected_chat_model.reason, 'inference_check_in_progress');
    assert.equal(helpers.chatDisabled(received, false, model), true);
  }
  report.capabilities.chat = { state: 'available', reason: 'inference_verified' };
  report.selected_chat_model = { id: model, state: 'available', reason: 'inference_verified', expires_at: Date.now() / 1000 + 60 };
  await assert.rejects(health.checkModelInference(model), { status: 409 });

  global.fetch = async () => Response.json(report, { status: 401 });
  await assert.rejects(health.checkModelInference(model), { status: 401 });
});

function visionFixture() {
  const report = fixture();
  report.vision_models = ['ollama/vision:7b', 'ollama/other-vision:7b'];
  report.installed_models = ['qwen:7b', 'vision:7b', 'other-vision:7b'];
  report.chat_models = ['ollama/qwen:7b'];
  report.capabilities.ollama = { state: 'available', reason: 'inventory_observed' };
  for (const role of ['image_chat', 'vision', 'embedding']) report.models[role] = report.models.default_chat;
  report.selected_chat_model = { id: 'ollama/qwen:7b', state: 'unknown', reason: 'inference_not_checked' };
  report.selected_vision_model = { id: 'ollama/vision:7b', state: 'unknown', reason: 'inference_not_checked' };
  return report;
}

test('image attachment eligibility requires fresh evidence for the exact configured vision model', () => {
  const report = visionFixture();
  const id = report.selected_vision_model.id;
  assert.equal(helpers.chatDisabled(report, true, id), true);
  report.capabilities.multimodal = { state: 'available', reason: 'inference_verified' };
  report.selected_vision_model = {
    id, state: 'available', reason: 'inference_verified',
    expires_at: Date.now() / 1000 + 60, identity_evidence: 'provider_digest',
  };
  assert.equal(helpers.chatDisabled(helpers.parseCapabilityReport(report), true, id), false);
  assert.equal(helpers.chatDisabled(report, true, 'ollama/other-vision:7b'), true);
  assert.equal(helpers.chatDisabled(report, true, 'ollama/qwen:7b'), true);
  report.selected_vision_model.identity_evidence = 'unavailable';
  assert.equal(helpers.chatDisabled(report, true, id), true);
  report.selected_vision_model.identity_evidence = 'provider_digest';
  report.selected_vision_model.expires_at = Date.now() / 1000 - 1;
  assert.equal(helpers.chatDisabled(report, true, id), true);
});

test('Models exposes a separate configured vision selection and a readiness action', () => {
  const report = visionFixture();
  const { ModelScanner } = loadTs('../src/components/models/ModelScanner.tsx', {
    '../../api/capabilities': helpers,
    '../../context/WorkbenchContext': { useWorkbench: () => ({
      capabilities: report, availableModels: report.chat_models, defaultModel: 'ollama/qwen:7b',
      isBackendConnected: true, isCheckingHealth: false, selectedModel: 'ollama/qwen:7b',
      selectedVisionModel: 'ollama/other-vision:7b',
    }) },
  });
  const html = renderToStaticMarkup(React.createElement(ModelScanner));
  const picker = html.match(/<select[^>]*id="vision-model"[\s\S]*?<\/select>/)?.[0];
  assert.ok(picker);
  assert.match(picker, /other-vision:7b" selected=""/);
  assert.doesNotMatch(picker, /qwen/);
  assert.match(html, /Check vision inference/);
});

test('explicit vision check sends a separate model and preserves a safe conflict reason', async (t) => {
  const client = loadTs('../src/api/client.ts');
  const health = loadTs('../src/api/health.ts', { './client': client, './capabilities': helpers });
  const originalFetch = global.fetch;
  t.after(() => { global.fetch = originalFetch; });
  const report = visionFixture();
  report.selected_vision_model.reason = 'vision_model_digest_required';
  global.fetch = async (path, options) => {
    assert.equal(path, '/api/health/capabilities/vision-check?model=ollama%2Fqwen%3A7b');
    assert.equal(options.method, 'POST');
    assert.deepEqual(JSON.parse(options.body), { model: 'ollama/vision:7b' });
    return Response.json(report, { status: 409 });
  };
  const result = await health.checkVisionInference('ollama/vision:7b', 'ollama/qwen:7b');
  assert.equal(result.selected_vision_model.reason, 'vision_model_digest_required');
  assert.equal(helpers.chatDisabled(result, true, 'ollama/vision:7b'), true);
});
