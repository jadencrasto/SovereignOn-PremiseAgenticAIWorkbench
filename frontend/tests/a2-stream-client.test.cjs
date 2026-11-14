const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const filename = path.resolve(__dirname, '../src/api/client.ts');
const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: { module: ts.ModuleKind.CommonJS }}).outputText;
const mod = new Module(filename, module); mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename)); mod._compile(output, filename);
const { streamSSE } = mod.exports;
function response(parts) {
  return new Response(new ReadableStream({start(c) { for (const p of parts) c.enqueue(new TextEncoder().encode(p)); c.close(); }}), {status: 200});
}
function callbacks(events) { return {onDelta: x => events.push(['delta', x]), onDone: (s) => events.push(['done', s]), onError: x => events.push(['error', x]), onSources: () => {}}; }
test('stream submits JSON and delivers ordered fragments and completion', async () => {
 const events = []; let request;
 global.fetch = async (_url, options) => { request = options; return response(['data: {"type":"delta","content":"He', 'llo"}\n\ndata: {"type":"delta","content":"!"}\n\ndata: {"type":"done","session_id":"s"}\n\n']); };
 await streamSSE('/api/chat', {message:'hello'}, callbacks(events));
 assert.equal(JSON.parse(request.body).message, 'hello');
 assert.deepEqual(events, [['delta','Hello'],['delta','!'],['done','s']]);
});
test('interrupted and invalid streams are errors, not completion', async () => {
 for (const text of ['data: {"type":"delta","content":"part"}\n\n', 'data: broken\n\n']) {
   const events = []; global.fetch = async () => response([text]);
   await streamSSE('/api/chat', {}, callbacks(events));
   assert.equal(events.filter(e => e[0] === 'error').length, 1);
   assert.equal(events.some(e => e[0] === 'done'), false);
 }
});

const chatFilename = path.resolve(__dirname, '../src/api/chat.ts');
const chatOutput = ts.transpileModule(fs.readFileSync(chatFilename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
const chatModule = new Module(chatFilename, module); chatModule.filename = chatFilename; chatModule.paths = Module._nodeModulePaths(path.dirname(chatFilename));
chatModule.require = name => name === './client' ? mod.exports : require(name);
chatModule._compile(chatOutput, chatFilename);
test('abandoned generation callbacks cannot alter the new conversation', () => {
 const events = []; let active = true;
 const guarded = chatModule.exports.guardChatCallbacks({onDelta: text => events.push(text), onDone: () => events.push('done'), onError: text => events.push(text)}, () => active);
 guarded.onDelta('first'); active = false;
 guarded.onDelta('late'); guarded.onDone(); guarded.onError('late error');
 assert.deepEqual(events, ['first']);
});
test('provider error remains a terminal error', async () => {
 const events = [];
 global.fetch = async () => response(['data: {"type":"error","content":"Selected model is unavailable"}\n\ndata: {"type":"done"}\n\n']);
 await streamSSE('/api/chat', {}, callbacks(events));
 assert.deepEqual(events, [['error','Selected model is unavailable']]);
});
test('terminal event cancels an otherwise open response body', async () => {
 const events=[]; let cancelled=false;
 global.fetch=async()=>new Response(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('data: {"type":"done","session_id":"s"}\n\n'))},cancel(){cancelled=true}}),{status:200});
 await streamSSE('/api/chat',{},callbacks(events));
 assert.equal(cancelled,true); assert.deepEqual(events,[['done','s']]);
});
