const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

test('artifact viewer independently withholds preview and download controls', () => {
  const filename = path.resolve(__dirname, '../src/components/artifacts/ArtifactViewer.tsx');
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true,
  }}).outputText;
  const mod = new Module(filename, module);
  mod.filename = filename;
  mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = mod.require.bind(mod);
  mod.require = name => name === '../../context/WorkbenchContext'
    ? { useWorkbench: () => ({ addToast() {} }) } : original(name);
  mod._compile(output, filename);
  const html = renderToStaticMarkup(React.createElement(mod.exports.ArtifactViewer));
  assert.match(html, /preview and download are blocked/i);
  assert.doesNotMatch(html, /<a\b|<button\b|\/api\/artifacts/i);
});
