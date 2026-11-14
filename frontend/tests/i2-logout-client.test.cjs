const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module');
const ts = require('typescript');
const filename = path.resolve(__dirname, '../src/api/auth.ts');
const js = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const mod = new Module(filename, module); mod.filename=filename; mod.paths=Module._nodeModulePaths(path.dirname(filename)); mod._compile(js, filename);
test('logout confirms success and surfaces server or transport failures', async () => {
  const original = global.fetch;
  try {
    let request;
    global.fetch = async (url, options) => { request={url,options}; return {ok:true}; };
    await mod.exports.logoutApi();
    assert.equal(request.url, '/api/auth/logout');
    assert.equal(request.options.method, 'POST');
    assert.equal(request.options.credentials, 'include');
    assert.equal(request.options.headers['X-Requested-With'], 'XMLHttpRequest');
    global.fetch = async () => ({ok:false, status:503});
    await assert.rejects(mod.exports.logoutApi(), /server session may still be active/);
    global.fetch = async () => { throw new Error('synthetic network failure'); };
    await assert.rejects(mod.exports.logoutApi(), /synthetic network failure/);
  } finally { global.fetch=original; }
});
