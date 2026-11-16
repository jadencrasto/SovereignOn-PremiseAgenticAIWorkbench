const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

// Evaluate the actual config with inert plugins, without starting Vite.
const filename = path.resolve(__dirname, '../vite.config.ts');
const source = fs.readFileSync(filename, 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true },
});
const configModule = new Module(filename, module);
configModule.filename = filename;
configModule.require = (name) => {
  if (name === 'vite') return { defineConfig: (config) => config };
  if (name === '@vitejs/plugin-react' || name === '@tailwindcss/vite') return () => ({ name });
  throw new Error(`Unexpected config import: ${name}`);
};
configModule._compile(outputText, filename);
const config = configModule.exports.default;

function setPortForTest(t, port) {
  const previous = process.env.BACKEND_PORT;
  t.after(() => {
    if (previous === undefined) delete process.env.BACKEND_PORT;
    else process.env.BACKEND_PORT = previous;
  });
  if (port === undefined) delete process.env.BACKEND_PORT;
  else process.env.BACKEND_PORT = port;
}

test('Vite development routes the same /api path to the configured loopback port', (t) => {
  setPortForTest(t, '8001');
  const { server } = config({ command: 'serve', isPreview: false });
  assert.equal(server.port, 5173);
  assert.deepEqual(server.proxy['/api'], { target: 'http://127.0.0.1:8001', changeOrigin: true });
});

test('build and preview ignore the development override and dev defaults to port 8000', (t) => {
  setPortForTest(t, '8001');
  for (const env of [{ command: 'build' }, { command: 'serve', isPreview: true }]) {
    assert.equal(config(env).server.proxy['/api'].target, 'http://127.0.0.1:8000');
  }
  delete process.env.BACKEND_PORT;
  assert.equal(config({ command: 'serve' }).server.proxy['/api'].target, 'http://127.0.0.1:8000');
});

test('invalid development port fails before falling back to a backend on another port', (t) => {
  setPortForTest(t, '0');
  for (const port of ['0', '65536', '8e3', '8001/path', 'http://example.com:8001']) {
    process.env.BACKEND_PORT = port;
    assert.throws(() => config({ command: 'serve' }), /BACKEND_PORT must be a decimal port/);
  }
});
