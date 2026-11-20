const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const filename = path.resolve(__dirname, '../src/api/client.ts');
const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: { module: ts.ModuleKind.CommonJS }}).outputText;
const mod = new Module(filename, module); mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename)); mod._compile(output, filename);
const { streamSSE, streamSSEFromFormData } = mod.exports;
function response(parts) {
  return new Response(new ReadableStream({start(c) { for (const p of parts) c.enqueue(new TextEncoder().encode(p)); c.close(); }}), {status: 200});
}
function callbacks(events) { return {onDelta: x => events.push(['delta', x]), onDone: (s) => events.push(['done', s]), onError: x => events.push(['error', x]), onSources: () => {}}; }
test('image chat submits both model IDs and disables tools in the multipart request', async (t) => {
 const chatFile = path.resolve(__dirname, '../src/api/chat.ts');
 const chat = new Module(chatFile, module);
 chat.filename = chatFile; chat.paths = Module._nodeModulePaths(path.dirname(chatFile));
 const original = chat.require.bind(chat);
 chat.require = name => name === './client' ? mod.exports : original(name);
 chat._compile(ts.transpileModule(fs.readFileSync(chatFile, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
 }).outputText, chatFile);
 const originalFetch = global.fetch;
 t.after(() => { global.fetch = originalFetch; });
 global.fetch = async (url, options) => {
  assert.equal(url, '/api/chat/multimodal');
  assert.equal(options.body.get('model'), 'ollama/text:7b');
  assert.equal(options.body.get('vision_model'), 'ollama/vision:7b');
  assert.equal(options.body.get('tools_enabled'), 'false');
  assert.equal(options.body.get('image').name, 'sample.png');
  return response(['data: {"type":"done","session_id":"s"}\n\n']);
 };
 const events = [];
 const image = Object.assign(new Blob(['synthetic'], { type: 'image/png' }), { name: 'sample.png' });
 await chat.exports.streamChatMultimodal({
  message: 'Describe this image', model: 'ollama/text:7b', vision_model: 'ollama/vision:7b',
  tools_enabled: true, image,
 }, callbacks(events));
 assert.deepEqual(events, [['done', 's']]);
});

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

test('identified streams ignore duplicates and reject gaps or another attempt', async (t) => {
 const originalFetch = global.fetch;
 t.after(() => { global.fetch = originalFetch; });
 const key = {session_id: 's', turn_id: 'turn-id', attempt: 2};
 const event = (sequence, type, content = '', overrides = {}) =>
  'data: ' + JSON.stringify({...key, sequence, type, content, ...overrides}) + '\n\n';
 const accepted = event(1, 'accepted');
 const delta = event(2, 'delta', 'Hello');
 const done = event(3, 'done');
 global.fetch = async () => response([accepted, delta, delta, done, done]);
 const events = [];
 await streamSSE('/api/chat', key, {...callbacks(events), onAccepted: () => events.push(['accepted'])});
 assert.deepEqual(events, [['accepted'], ['delta', 'Hello'], ['done', 's']]);
 for (const invalid of [event(3, 'delta', 'out of order'), event(2, 'done', '', {attempt: 1})]) {
  const before = [];
  global.fetch = async () => response([accepted, invalid, delta, done]);
  await streamSSE('/api/chat', key, callbacks(before));
  assert.deepEqual(before, [['error', 'Invalid stream response']]);
 }
 const replay = [];
 global.fetch = async () => response([event(1, 'done')]);
 await streamSSE('/api/chat', key, callbacks(replay));
 assert.deepEqual(replay, [['done', 's']]);
});

test('chat clients turn FastAPI validation details into a displayable error', async (t) => {
 const originalFetch = global.fetch;
 t.after(() => { global.fetch = originalFetch; });
 global.fetch = async () => Response.json({detail: [{loc: ['body', 'message'], msg: 'Field required'}]}, {status: 422});
 for (const send of [
  handlers => streamSSE('/api/chat', {}, handlers),
  handlers => streamSSEFromFormData('/api/chat/multimodal', new FormData(), handlers),
 ]) {
  const events = [];
  await send(callbacks(events));
  assert.deepEqual(events, [['error', 'The chat request is invalid.']]);
 }
});

test('a local text turn separates waiting for the model from the first token', async t => {
 const originalFetch = global.fetch;
 t.after(() => { global.fetch = originalFetch; });
 const turn = {session_id: 's', turn_id: 'pending', attempt: 1};
 const event = (sequence, type, content) => `data: ${JSON.stringify({...turn, sequence, type, content})}\n\n`;
 const wire = [event(1, 'accepted', ''), event(2, 'agent_status', 'awaiting_model'),
  event(3, 'delta', 'Hi'), event(4, 'done', '')];
 global.fetch = async () => response(wire);
 const observed = [];
 await streamSSE('/api/chat', turn, {...callbacks(observed), onAgentStatus: status => observed.push(['status', status])});
 assert.deepEqual(observed, [['status', 'awaiting_model'], ['delta', 'Hi'], ['done', 's']]);
 const vision = [];
 await streamSSE('/api/chat', {...turn, image_id: 'image'}, callbacks(vision));
 assert.deepEqual(vision, [['error', 'Invalid stream response']]);
});

const chatFilename = path.resolve(__dirname, '../src/api/chat.ts');
const chatOutput = ts.transpileModule(fs.readFileSync(chatFilename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
const chatModule = new Module(chatFilename, module); chatModule.filename = chatFilename; chatModule.paths = Module._nodeModulePaths(path.dirname(chatFilename));
chatModule.require = name => name === './client' ? mod.exports : require(name);
chatModule._compile(chatOutput, chatFilename);

test('the batch wire contract preserves ordered duplicate names, data and selected source metadata', async t => {
 const previous = global.fetch;
 t.after(() => { global.fetch = previous; });
 const files = [
  new File(['Name,V\nOne,1\n'], 'data.csv', { type: 'text/csv' }),
  new File(['Name,V\nTwo,2\n'], 'data.csv', { type: 'text/csv' }),
  new File(['note'], 'note.txt', { type: 'text/plain' }),
 ];
 const selected = {
  attachment_id: 'source-2', filename: 'data.csv', mime_type: 'text/csv', size_bytes: files[1].size,
 };
 global.fetch = async (url, options) => {
  assert.equal(url, '/api/chat/text-attachments');
  const actual = options.body.getAll('file');
  assert.deepEqual(actual.map(file => file.name), ['data.csv', 'data.csv', 'note.txt']);
  assert.deepEqual(await Promise.all(actual.map(file => file.text())), ['Name,V\nOne,1\n', 'Name,V\nTwo,2\n', 'note']);
  assert.equal(options.body.get('source'), '2');
  assert.equal(options.body.get('row_start'), '1');
  assert.equal(options.body.has('line_start'), false);
  return response(['data: ' + JSON.stringify({ type: 'done', session_id: 's', attachment: selected }) + '\n\n']);
 };
 const seen = [];
 await chatModule.exports.streamChatTextFiles({ files, source: 2 }, 's', 'ollama/text', 'Find maximum V', {
  ...callbacks(seen), onDone: (_session, _model, source) => seen.push(source),
 }, new AbortController().signal, { start: 1, end: 1 });
 assert.deepEqual(seen, [selected]);
});
test('the existing done event carries both source identities for a cross-file comparison', async t => {
 const previous = global.fetch;
 t.after(() => { global.fetch = previous; });
 const files = [new File(['Maximum pressure: 12 bar'], 'limits.txt', { type: 'text/plain' }),
  new File(['Pressure (bar)\n13\n'], 'readings.csv', { type: 'text/csv' })];
 const selected = { attachment_id: 'source-2', filename: files[1].name, mime_type: 'text/csv', size_bytes: files[1].size };
 const paired = [{ role: 'limits', attachment_id: 'source-1', filename: files[0].name,
  mime_type: 'text/plain', size_bytes: files[0].size }, { role: 'measurements', ...selected }];
 global.fetch = async (url, options) => {
  assert.equal(url, '/api/chat/text-attachments');
  assert.equal(options.body.get('message'), 'Compare readings.csv against limits.txt.');
  assert.equal(options.body.get('source'), '2');
  const done = { type: 'done', session_id: 's', model_used: 'Deterministic file analysis',
   attachment: selected, file_sources: paired };
  return response(['data: ' + JSON.stringify(done) + '\n\ndata: ' + JSON.stringify(done) + '\n\n']);
 };
 const seen = [];
 await chatModule.exports.streamChatTextFiles({ files, source: 2 }, 's', 'ollama/text', 'Compare readings.csv against limits.txt.', {
  ...callbacks(seen), onDone: (...args) => seen.push(args),
 }, new AbortController().signal, { start: 1, end: 1 });
 assert.deepEqual(seen, [['s', 'Deterministic file analysis', selected, paired]]);
});

test('text preview and questions use separate multipart requests with only allowed fields', async t => {
 const originalFetch = global.fetch;
 t.after(() => { global.fetch = originalFetch; });
 const file = new File(['café'], 'note.txt', { type: 'text/plain' });
 const signal = new AbortController().signal;
 const requests = [];
 let range;
 global.fetch = async (url, options) => {
  requests.push(url);
  assert.equal(options.signal, signal);
  assert.equal(options.body.get('session_id'), 's');
  assert.equal(await options.body.get('file').text(), 'café');
  const rangeFields = range ? ['line_start', 'line_end'] : [];
  assert.equal(options.body.get('line_start'), range ? String(range.start) : null);
  assert.equal(options.body.get('line_end'), range ? String(range.end) : null);
  if (url === '/api/chat/text-preview') {
   assert.deepEqual([...options.body.keys()].sort(), ['file', 'session_id', ...rangeFields].sort());
   assert.equal(options.cache, 'no-store');
   return Response.json({ filename: 'note.txt', size_bytes: 5, line_count: 1, text: 'café', truncated: false });
  }
  assert.equal(url, '/api/chat/text-file');
  assert.deepEqual([...options.body.keys()].sort(), ['file', 'message', 'model', 'session_id', ...rangeFields].sort());
  assert.equal(options.body.get('model'), 'ollama/text:7b');
  return response(['data: {"type":"error","content":"Selected model is unavailable"}\n\ndata: {"type":"done"}\n\n']);
 };
 for (const selectedRange of [undefined, { start: 1, end: 1 }]) {
  range = selectedRange;
  assert.equal((await chatModule.exports.previewTextFile(file, 's', signal, range)).text, 'café');
  const events = [];
  await chatModule.exports.streamChatTextFile(file, 's', 'ollama/text:7b', 'What is written?', callbacks(events), signal, range);
  assert.deepEqual(events, [['error', 'Selected model is unavailable']]);
 }
 assert.deepEqual(requests, ['/api/chat/text-preview', '/api/chat/text-file', '/api/chat/text-preview', '/api/chat/text-file']);
});

test('abandoned generation callbacks cannot alter the new conversation', () => {
 const events = []; let active = true;
 const guarded = chatModule.exports.guardChatCallbacks({onDelta: text => events.push(text), onDone: () => events.push('done'), onError: text => events.push(text)}, () => active);
 guarded.onDelta('first'); active = false;
 guarded.onDelta('late'); guarded.onDone(); guarded.onError('late error');
 assert.deepEqual(events, ['first']);
});

test('CSV preview and analysis use CSV endpoints and send one inclusive row pair', async t => {
 const originalFetch = global.fetch;
 t.after(() => { global.fetch = originalFetch; });
 const file = new File(['Name,Value\nExample,12\n'], 'sample.csv', { type: 'text/csv' });
 const signal = new AbortController().signal;
 const range = { start: 1, end: 1 };
 const paths = [];
 global.fetch = async (path, options) => {
  paths.push(path);
  assert.equal(options.signal, signal);
  assert.equal(options.body.get('row_start'), '1');
  assert.equal(options.body.get('row_end'), '1');
  assert.equal(options.body.has('line_start'), false);
  assert.equal(options.body.get('file').name, 'sample.csv');
  if (path === '/api/chat/csv-preview') return Response.json({ rows: [['Example', '12']] });
  assert.equal(options.body.get('model'), 'ollama/text:7b');
  return response(['data: {"type":"error","content":"Selected model is unavailable"}\n\n']);
 };
 assert.deepEqual((await chatModule.exports.previewCsvFile(file, 's', signal, range)).rows, [['Example', '12']]);
 const events = [];
 await chatModule.exports.streamChatTextFile(file, 's', 'ollama/text:7b', 'What is selected?', callbacks(events), signal, range);
 assert.deepEqual(paths, ['/api/chat/csv-preview', '/api/chat/csv-file']);
 assert.deepEqual(events, [['error', 'Selected model is unavailable']]);
});

test('multipart file analysis keeps step summaries and the existing terminal error code', async t => {
 const originalFetch = global.fetch;
 t.after(() => { global.fetch = originalFetch; });
 const data = 'Name,Value\nPump A,12\n';
 const file = new File([data], 'original.csv', { type: 'text/csv' });
 const wire = [
  {type:'plan_created',content:'',plan:{objective:'Analyze the selected CSV rows',steps:[
   {id:'file_step_1',description:'Filter selected rows',status:'pending',requires_approval:false},
  ]}},
  {type:'plan_step',content:'',step_id:'file_step_1',status:'completed',summary:'Filter matched 1 selected row.'},
  {type:'error',content:'Step 2 did not complete.',error_code:'TEXT_WORKFLOW_STEP_FAILED'},
  {type:'done',content:'',session_id:'s',model_used:'Deterministic file analysis'},
 ].map(event => 'data: ' + JSON.stringify(event) + '\n\n').join('');
 global.fetch = async (url, options) => {
  assert.equal(url, '/api/chat/csv-file');
  assert.equal(options.body.get('file').name, 'original.csv');
  assert.equal(await options.body.get('file').text(), data);
  return response([wire.slice(0, 47), wire.slice(47)]);
 };
 const seen = [];
 await chatModule.exports.streamChatTextFile(file, 's', 'ollama/text', 'Filter and count', {
  ...callbacks(seen),
  onPlanCreated: plan => seen.push(['plan', plan.task_id, plan.plan.steps[0].id]),
  onPlanStep: step => seen.push(['step', step.summary]),
  onError: (message, info) => seen.push(['error', message, info.code]),
 }, new AbortController().signal, {start:1,end:1});
 assert.deepEqual(seen, [
  ['plan', undefined, 'file_step_1'],
  ['step', 'Filter matched 1 selected row.'],
  ['error', 'Step 2 did not complete.', 'TEXT_WORKFLOW_STEP_FAILED'],
 ]);
});

test('file preview and stream forward existing HTTP response status and error codes', async t => {
 const originalFetch = global.fetch;
 t.after(() => { global.fetch = originalFetch; });
 const file = new File(['notes'], 'original.txt', { type: 'text/plain' });
 const signal = new AbortController().signal;
 global.fetch = async () => Response.json({detail:'Sign in again.'}, {
  status:401, headers:{'X-Error-Code':'TEXT_FILE_AUTH_CHANGED'},
 });
 await assert.rejects(chatModule.exports.previewTextFile(file, 's', signal),
  { status:401, code:'TEXT_FILE_AUTH_CHANGED' });
 const seen = [];
 await chatModule.exports.streamChatTextFile(file, 's', 'ollama/text', 'Find a passage', {
  ...callbacks(seen), onError: (_message, info) => seen.push(info),
 }, signal);
 assert.deepEqual(seen, [{status:401,code:'TEXT_FILE_AUTH_CHANGED'}]);
});
test('malformed done after provider error remains a terminal error', async () => {
 const events = [];
 global.fetch = async () => response(['data: {"type":"error","content":"Selected model is unavailable"}\n\ndata: {"type":"done"}\n\n']);
 await streamSSE('/api/chat', {}, callbacks(events));
 assert.deepEqual(events, [['error','Selected model is unavailable']]);
});
test('malformed duplicate and late events after failed or cancelled tasks are ignored', async () => {
 for (const status of ['task_failed', 'task_cancelled']) {
  const events = [];
  global.fetch = async () => response([`data: {"type":"${status}","task_id":"t"}\n\ndata: {"type":"${status}","task_id":"t"}\n\ndata: {"type":"delta","content":"late"}\n\ndata: {"type":"done"}\n\n`]);
  await streamSSE('/api/chat', {}, {...callbacks(events), onTaskStatus: s => events.push(['task', s])});
  assert.deepEqual(events, [['task', status]]);
 }
});
test('terminal event cancels an otherwise open response body', async () => {
 const events=[]; let cancelled=false;
 global.fetch=async()=>new Response(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('data: {"type":"done","session_id":"s"}\n\n'))},cancel(){cancelled=true}}),{status:200});
 await streamSSE('/api/chat',{},callbacks(events));
 assert.equal(cancelled,true); assert.deepEqual(events,[['done','s']]);
});

test('identified local tool events expose only calculator status for the current attempt', async () => {
 const turn = {session_id:'s', turn_id:'local-turn', attempt:2};
 const frame = (event, index) => 'data: ' + JSON.stringify({ ...turn, sequence:index + 1, content:'', ...event }) + '\n\n';
 for (const invalid of [false, true]) {
  const activity = [];
  const events = [];
  global.fetch = async () => response([[
   {type:'accepted'},
   {type:'agent_status', content:'planning'},
   {type:'agent_status', content:'tool_selected'},
   {type:'tool_start', tool:'calculator'},
   {type:'tool_result', tool:'calculator', success:true, ...(invalid ? {tool_args:{expression:'private'}} : {})},
   {type:'done'},
  ].map(frame).join('')]);
  await streamSSE('/api/chat', turn, {
   ...callbacks(events),
   onAgentStatus: status => activity.push(status),
   onToolEvent: event => activity.push([event.type,event.tool,event.success]),
  });
  assert.deepEqual(activity, ['planning', 'tool_selected', ['tool_start','calculator',undefined],
   ...(invalid ? [] : [['tool_result','calculator',true]])]);
  assert.deepEqual(events, invalid ? [['error','Invalid stream response']] : [['done','s']]);
 }
});

test('local conversion status names the tool and keeps other tools outside the stream contract', async () => {
 const turn = {session_id:'s', turn_id:'local-conversion', attempt:1};
 for (const tool of ['unit_conversion', 'file_read']) {
  const events = [];
  const wire = [
   {type:'accepted', content:''},
   {type:'agent_status', content:'tool_selected', tool},
   {type:'tool_start', content:'', tool},
   {type:'tool_result', content:'', tool, success:true},
   {type:'done', content:''},
  ].map((event, index) => 'data: ' + JSON.stringify({...turn, ...event, sequence:index + 1}) + '\n\n');
  global.fetch = async () => response(wire);
  await streamSSE('/api/chat', turn, {
   ...callbacks(events),
   onAgentStatus: (status, selected) => events.push([status, selected]),
   onToolEvent: event => events.push([event.type, event.tool]),
  });
  assert.deepEqual(events, tool === 'unit_conversion'
   ? [['tool_selected', tool], ['tool_start', tool], ['tool_result', tool], ['done', 's']]
   : [['error', 'Invalid stream response']]);
 }
});

test('visual status is accepted only for an identified image request', async () => {
 const turn = {session_id:'s', turn_id:'image-turn', attempt:1};
 const wire = [
  {type:'accepted', content:''},
  {type:'agent_status', content:'analyzing_image'},
  {type:'done', content:''},
 ].map((event, index) => 'data: ' + JSON.stringify({...turn, ...event, sequence:index + 1}) + '\n\n');
 for (const useImage of [true, false]) {
  const events = [];
  global.fetch = async () => response(wire);
  await streamSSE('/api/chat', useImage ? {...turn, image_id:'owned-image'} : turn, {
   ...callbacks(events), onAgentStatus: status => events.push([status]),
  });
  assert.deepEqual(events, useImage ? [['analyzing_image'], ['done','s']] : [['error','Invalid stream response']]);
 }
});

test('an image workflow streams evidence and draft progress on the same attempt', async () => {
 const turn = {session_id:'s', turn_id:'image-workflow', attempt:2};
 const statuses = ['planning', 'retrieving_evidence', 'comparing_evidence', 'analyzing_image', 'drafting'];
 const wire = [
  {type:'accepted', content:''},
  ...statuses.map(content => ({type:'agent_status', content})),
  {type:'done', content:''},
 ].map((event, index) => 'data: ' + JSON.stringify({...turn, ...event, sequence:index + 1}) + '\n\n');
 const events = [];
 global.fetch = async () => response(wire);
 await streamSSE('/api/chat', {...turn, image_id:'owned-image', knowledge_source_ids:['owned-source']}, {
  ...callbacks(events), onAgentStatus: status => events.push([status]),
 });
 assert.deepEqual(events, [...statuses.map(status => [status]), ['done','s']]);
});
