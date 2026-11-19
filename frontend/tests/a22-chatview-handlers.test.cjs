// Deterministic hook harness exercising production ChatView handlers, not React DOM.
const {test}=require('node:test'), assert=require('node:assert/strict'), fs=require('fs'), path=require('path'), Module=require('module'), ts=require('typescript');
let slots, cursor, requests, effects, textBlocked=false, textModel='fake', authRefreshes=0;
const React={createElement:(type,props,...children)=>({type,props:{...props,children}}),useState(initial){let i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v}]},useRef(initial){let i=cursor++;return slots[i]??=( {current:initial} )},useCallback(f){cursor++;return f},useEffect(effect){cursor++;effects.push(effect)}};
function MessageList(){} function ChatInput(){} function ChatHistorySidebar(){}
let createImpl=async()=>({session_id:'created-server-id'});
let visionModel = 'ollama/vision:7b';
let localMode = false;
let localOffline = false;
let historyImpl = async () => ({id:'server-session',title:'Conversation',messages:[]});
const api={createSession:signal=>createImpl(signal),fetchSessions:async()=>({count:0}),streamChat(payload,callbacks,signal){let resolve;let p=new Promise(r=>resolve=r);requests.push({payload,callbacks,signal,resolve});return p},guardChatCallbacks(callbacks,active){return new Proxy(callbacks,{get:(t,p)=>(...a)=>{if(active())t[p](...a)}})}};
api.streamChatMultimodal = api.streamChat;
api.streamChatTextFile = (file, session_id, model, message, callbacks, signal, range) =>
  api.streamChat({ file, session_id, model, message, range }, callbacks, signal);
api.streamChatTextFiles = (batch, session_id, model, message, callbacks, signal, range) =>
  api.streamChat({ batch, session_id, model, message, range }, callbacks, signal);
const chatFile=path.resolve(__dirname,'../src/api/chat.ts');
const chatOut=ts.transpileModule(fs.readFileSync(chatFile,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const chatMod=new Module(chatFile,module);chatMod.filename=chatFile;chatMod.paths=Module._nodeModulePaths(path.dirname(chatFile));const chatRequire=chatMod.require.bind(chatMod);chatMod.require=n=>n==='./client'?{}:chatRequire(n);chatMod._compile(chatOut,chatFile);
api.guardChatCallbacks=chatMod.exports.guardChatCallbacks;
const localTurnsFile=path.resolve(__dirname,'../src/components/chat/localTurns.ts');
const localTurnsMod=new Module(localTurnsFile,module);
localTurnsMod._compile(ts.transpileModule(fs.readFileSync(localTurnsFile,'utf8'),{
  compilerOptions:{module:ts.ModuleKind.CommonJS},
}).outputText,localTurnsFile);
const mocks={react:React,'./MessageList':{MessageList},'./ChatInput':{ChatInput},'./ChatHistorySidebar':{ChatHistorySidebar,loadSession:async()=>({id:'server-session',title:'Conversation',messages:[]})},'../../context/AuthContext':{useAuth:()=>({user:{id:'a'},refreshUser:async()=>{authRefreshes++;}})},'../../api/chat':api,'../../context/WorkbenchContext':{useWorkbench:()=>({selectedModel:textModel,selectedVisionModel:visionModel,addToast(){},capabilities:{text_file_understanding:true},isCheckingHealth:false})},'../../api/capabilities':{capabilityFor:()=>({state:'available'}),chatDisabled:(_report,image)=>image?false:textBlocked,describeCapability:()=>'',permitsCapability:()=>false},'../../api/tasks':{},'lucide-react':{Clock:()=>null,Plus:()=>null}};
mocks['./localTurns']=localTurnsMod.exports;
mocks['./ChatHistorySidebar'].loadSession = id => historyImpl(id);
const mockWorkbench = mocks['../../context/WorkbenchContext'].useWorkbench;
mocks['../../context/WorkbenchContext'].useWorkbench = () => {
  const state = mockWorkbench();
  return localMode ? { ...state, runProfile: 'local_dev',
    capabilities: localOffline ? null : { run_profile: 'local_dev' } } : state;
};
const file=path.resolve(__dirname,'../src/components/chat/ChatView.tsx');const out=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText;const mod=new Module(file,module);mod.filename=file;mod.paths=Module._nodeModulePaths(path.dirname(file));let orig=mod.require.bind(mod);mod.require=n=>Object.hasOwn(mocks,n)?mocks[n]:orig(n);mod._compile(out,file);
function render(){cursor=0;effects=[];return mod.exports.ChatView()} function find(n,t){if(!n||typeof n!=='object')return;if(n.type===t)return n;for(let c of n.props?.children||[]){let x=find(c,t);if(x)return x}} function parts(){let tree=render();return {tree,input:find(tree,ChatInput).props,list:find(tree,MessageList).props,sidebar:find(tree,ChatHistorySidebar)?.props}} function setup(){slots=[];requests=[];parts();slots[3]='server-session';return parts()}
test('a second submission from the same render cannot replace the active stream', async () => {
  const { input } = setup();
  const first = input.onSendMessage('First question');
  const duplicate = input.onSendMessage('First question');
  const submittedCount = requests.length;
  requests[0].callbacks.onDelta('First answer');
  requests[0].callbacks.onDone('server-session', 'fake');
  requests.forEach(request => request.resolve());
  await Promise.all([first, duplicate]);

  assert.equal(submittedCount, 1);
  assert.deepEqual(parts().list.messages.map(message => message.content), ['First question', 'First answer']);
  assert.equal(parts().input.isStreaming, false);

  const next = parts().input.onSendMessage('Next question');
  assert.equal(requests.length, 2);
  requests[1].callbacks.onDone('server-session', 'fake');
  requests[1].resolve();
  await next;
});
test('image chat pins the selected vision model, shows progress and handles a terminal error', async (t) => {
  const { input } = setup();
  const image = Object.assign(new Blob(['synthetic'], { type: 'image/png' }), { name: 'sample.png' });
  const pending = input.onSendMessage('Describe', image);
  t.after(() => URL.revokeObjectURL(parts().list.messages[0].attachments[0].objectUrl));
  await input.onSendMessage('Describe', image);
  assert.equal(requests.length, 1);
  const request = requests[0];
  assert.equal(request.payload.image, image);
  assert.equal(request.payload.vision_model, 'ollama/vision:7b');
  assert.equal(parts().list.messages[1].imageProgress, 'uploading');
  request.callbacks.onAgentStatus('analyzing_image');
  assert.equal(parts().list.messages[1].imageProgress, 'analyzing_image');
  request.callbacks.onAgentStatus('reasoning');
  assert.equal(parts().list.messages[1].imageProgress, 'reasoning');
  request.callbacks.onError('The local vision model could not analyze the image.');
  request.resolve();
  await pending;
  const reply = parts().list.messages[1];
  assert.equal(reply.error, true);
  assert.equal(reply.content, '');
  assert.equal(reply.isStreaming, false);
});

test('auth-driven workbench unmount aborts image reception and drops late callbacks', async () => {
  const { input } = setup();
  const releasePreviews = effects[0]();
  const abortRequest = effects[1]();
  const image = Object.assign(new Blob(['synthetic'], { type: 'image/png' }), { name: 'sample.png' });
  const pending = input.onSendMessage('Describe', image);
  const preview = parts().list.messages[0].attachments[0].objectUrl;
  abortRequest();
  releasePreviews();
  assert.equal(require('node:buffer').resolveObjectURL(preview), undefined);
  const request = requests[0];
  assert.equal(request.signal.aborted, true);
  request.callbacks.onDelta('late image answer');
  request.callbacks.onDone('server-session', 'fake');
  request.resolve();
  await pending;
  assert.equal(parts().list.messages[1].content, '');
});

function syntheticImage() {
  return Object.assign(new Blob(['synthetic'], { type: 'image/png' }), { name: 'sample.png' });
}

test('a text-file turn honors model readiness and same-render duplicate protection', async () => {
  const { input } = setup();
  const file = { name: 'note.txt', type: 'text/plain', size: 16 };
  const range = { start: 2, end: 3 };
  textBlocked = true;
  try {
    await input.onSendTextFile('What color?', file, range);
    assert.deepEqual(requests, []);
    assert.equal(parts().input.textFileSupported, true);
    assert.equal(parts().input.imageBlocked, true);
    assert.match(parts().input.sendStatus, /Verify the selected text model/);
  } finally {
    textBlocked = false;
  }
  const pending = input.onSendTextFile('What color?', file, range);
  await input.onSendTextFile('Duplicate', file);
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0].payload, { file, session_id: 'server-session', model: 'fake', message: 'What color?', range });
  assert.equal(parts().list.messages[0].attachments[0].mimeType, 'text/plain');
  assert.equal(parts().list.messages[0].attachments[0].objectUrl, undefined);
  assert.equal(parts().list.messages[1].textFileAnswer, true);
  requests[0].callbacks.onError('The local model could not answer the text-file question.');
  requests[0].resolve();
  await pending;
  assert.match(parts().list.messages[1].errorMessage, /could not answer/);
  assert.equal(parts().input.isStreaming, false);
});

test('conversation switches abort a text-file question and suppress its late answer', async () => {
  const view = setup();
  const pending = view.input.onSendTextFile('What color?', { name: 'note.txt', type: 'text/plain', size: 16 });
  const request = requests[0];
  view.tree.props.children[0].props.children[0].props.children[0].props.onClick();
  parts().sidebar.onSelectSession({ id: 'other', title: 'Other', messages: [] });
  assert.equal(request.signal.aborted, true);
  request.callbacks.onDelta('late private answer');
  request.callbacks.onDone('server-session', 'fake');
  request.resolve();
  await pending;
  assert.deepEqual(parts().list.messages, []);
});

const csv = { name: 'readings.csv', type: 'text/csv', size: 54 };
const rowRange = { start: 2, end: 5 };

test('batch answer is revealed only after matching the terminal source identity', async () => {
  const { input } = setup();
  const batch = { files: [csv, { ...csv }], source: 2 };
  const pending = input.onSendTextFiles('Find the maximum', batch, rowRange);
  const { payload, callbacks, resolve } = requests[0];
  assert.deepEqual(payload, { batch, session_id: 'server-session', model: 'fake', message: 'Find the maximum', range: rowRange });
  callbacks.onDelta('Verified data row');
  assert.equal(parts().list.messages[1].content, '');
  callbacks.onDone('server-session', 'Deterministic file analysis', {
    attachment_id: 'source-2', filename: csv.name, mime_type: 'text/csv', size_bytes: csv.size,
  });
  resolve();
  await pending;
  assert.equal(parts().list.messages[1].fileSource, 2);
  assert.equal(parts().list.messages[1].content, 'Verified data row');
  assert.deepEqual(parts().list.messages[0].attachments.map(file => file.sourceNumber), [1, 2]);
});

test('a matching filename with a different source number cannot release a batch answer', async () => {
  const { input } = setup();
  const pending = input.onSendTextFiles('Find the maximum', { files: [csv, { ...csv }], source: 2 }, rowRange);
  const { callbacks, resolve } = requests[0];
  callbacks.onDelta('Withheld unmatched data');
  callbacks.onDone('server-session', 'Deterministic file analysis', {
    attachment_id: 'source-1', filename: csv.name, mime_type: 'text/csv', size_bytes: csv.size,
  });
  resolve();
  await pending;
  const reply = parts().list.messages[1];
  assert.match(reply.errorMessage, /could not be matched/);
  assert.equal(reply.content, '');
  assert.equal(reply.fileSource, undefined);
});

const txtLimits = { name: 'limits.txt', type: 'text/plain', size: 31 };
const measurements = { attachment_id: 'source-2', filename: csv.name, mime_type: 'text/csv', size_bytes: csv.size };
const limitSource = { role: 'limits', attachment_id: 'source-1', filename: txtLimits.name,
  mime_type: 'text/plain', size_bytes: txtLimits.size };

function thresholdPlan(format) {
  return { plan: { objective: `Compare TXT limits with ${format} readings`,
    steps: ['Extract TXT limits', format === 'JSON' ? 'Verify JSON fields and units' : 'Verify CSV columns and units',
      'Compare readings with limits'].map((description, index) => ({
      id: 'file_step_' + (index + 1), description, status: 'pending', requires_approval: false,
    })) } };
}

test('a cross-file result is released after checking both request-local identities', async () => {
  const { input } = setup();
  const pending = input.onSendTextFiles('Compare readings.csv against limits.txt.',
    { files: [txtLimits, csv], source: 2 }, rowRange);
  const { callbacks, resolve } = requests[0];
  const sources = [limitSource, { role: 'measurements', ...measurements }];
  callbacks.onPlanCreated(thresholdPlan('CSV'));
  callbacks.onDelta('One of three readings violated the limit.');
  assert.equal(parts().list.messages[1].content, '');
  callbacks.onDone('server-session', 'Deterministic file analysis', measurements, sources);
  resolve();
  await pending;
  assert.equal(parts().list.messages[1].content, 'One of three readings violated the limit.');
  assert.deepEqual(parts().list.messages[1].fileSources, sources);
});

for (const [scenario, limits] of [
  ['wrong source ordinal', { ...limitSource, attachment_id: 'source-2' }],
  ['unexpected filename', { ...limitSource, filename: 'another.txt' }],
  ['wrong role', { ...limitSource, role: 'measurements' }],
  ['wrong size', { ...limitSource, size_bytes: 500 }],
]) {
  test('cross-file evidence is withheld for ' + scenario, async () => {
    const { input } = setup();
    const pending = input.onSendTextFiles('Compare readings.csv against limits.txt.',
      { files: [txtLimits, csv], source: 2 }, rowRange);
    const { callbacks, resolve } = requests[0];
    callbacks.onDelta('Withheld private result');
    callbacks.onDone('server-session', 'Deterministic file analysis', measurements,
      [limits, { role: 'measurements', ...measurements }]);
    resolve();
    await pending;
    const reply = parts().list.messages[1];
    assert.match(reply.errorMessage, /could not be matched/);
    assert.equal(reply.content, '');
    assert.equal(reply.fileSources, undefined);
  });
}
const localFilePlan = {
  plan: { objective: 'Analyze the selected CSV rows', steps: [
    { id: 'file_step_1', description: 'Filter selected rows', status: 'pending', requires_approval: false },
    { id: 'file_step_2', description: 'Calculate percentage of valid readings', status: 'pending', requires_approval: false },
  ] },
};

test('JSON comparison completion verifies the selected format and request-local evidence', async () => {
  const { input } = setup();
  const jsonFile = { name: 'equipment.json', type: 'application/json', size: 31 };
  const batch = { files: [txtLimits, jsonFile], source: 2 };
  const pending = input.onSendTextFiles('Compare equipment.json against limits.txt.', batch);
  const { callbacks, resolve } = requests[0];
  const jsonSource = { role: 'measurements', attachment_id: 'source-2', filename: jsonFile.name,
    mime_type: 'application/json', size_bytes: jsonFile.size };
  callbacks.onPlanCreated(thresholdPlan('JSON'));
  callbacks.onDelta('JSON record index 1 at /equipment/1');
  assert.equal(parts().list.messages[1].content, '');
  callbacks.onDone('server-session', 'Deterministic file analysis', jsonSource, [limitSource, jsonSource]);
  resolve();
  await pending;
  assert.equal(parts().list.messages[1].content, 'JSON record index 1 at /equipment/1');
  assert.deepEqual(parts().list.messages[1].fileSources, [limitSource, jsonSource]);
  assert.equal(parts().list.messages[0].attachments[1].mimeType, 'application/json');
});

test('a cross-file plan cannot release a result with only its measurement descriptor', async () => {
  const { input } = setup();
  const pending = input.onSendTextFiles('Compare readings.csv against limits.txt.',
    { files: [txtLimits, csv], source: 2 }, rowRange);
  const { callbacks, resolve } = requests[0];
  callbacks.onPlanCreated(thresholdPlan('CSV'));
  callbacks.onTaskStatus('task_completed', '');
  callbacks.onDelta('Result missing its limits identity');
  callbacks.onDone('server-session', 'Deterministic file analysis', measurements);
  resolve();
  await pending;
  const result = parts().list.messages[1];
  assert.equal(result.content, '');
  assert.equal(result.plan, undefined);
  assert.equal(result.workflowStatus, 'interrupted');
  assert.match(result.errorMessage, /could not be matched/);
});

for (const [step, reason] of [
  [1, 'TXT line 3 repeats or conflicts with another limit for the same parameter.'],
  [2, 'The units on TXT line 2 and JSON field B cannot be compared.'],
]) {
  test('a threshold workflow marks step ' + step + ' failed and keeps only the earlier verified progress', async () => {
    const { input } = setup();
    const file = { name: 'equipment.json', type: 'application/json', size: 78 };
    const pending = input.onSendTextFiles('Compare equipment.json against limits.txt.', { files: [txtLimits, file], source: 2 });
    const { callbacks, resolve } = requests[0];
    const plan = thresholdPlan('JSON');
    callbacks.onPlanCreated(plan);
    if (step === 2) callbacks.onPlanStep({ step_id: 'file_step_1', status: 'completed', summary: 'Explicit limits verified in the selected TXT source: 1.' });
    callbacks.onPlanStep({ step_id: 'file_step_' + step, status: 'failed' });
    callbacks.onError(`Step ${step} (${plan.plan.steps[step - 1].description}) did not complete. ${reason}`,
      { code: 'TEXT_WORKFLOW_STEP_FAILED' });
    resolve();
    await pending;
    const result = parts().list.messages[1];
    assert.equal(result.content, '');
    assert.equal(result.workflowStatus, 'failed');
    assert.match(result.errorMessage, /did not complete/);
    assert.ok(result.errorMessage.includes(reason));
    assert.equal(result.plan.steps[step - 1].status, 'failed');
    assert.equal(result.plan.steps[0].summary !== undefined, step === 2);
    assert.equal(result.fileSources, undefined);
  });
}

test('switching conversations drops buffered JSON comparison evidence and clears the composer', async (t) => {
  const previous = global.sessionStorage;
  const writes = [];
  global.sessionStorage = { setItem: (...args) => writes.push(args), removeItem() {} };
  t.after(() => { global.sessionStorage = previous; });
  const { input, tree } = setup();
  const file = { name: 'equipment.json', type: 'application/json', size: 78 };
  const pending = input.onSendTextFiles('Compare equipment.json against limits.txt.', { files: [txtLimits, file], source: 2 });
  const { callbacks, resolve, signal } = requests[0];
  callbacks.onPlanCreated(thresholdPlan('JSON'));
  callbacks.onDelta('Private JSON result at /equipment/1');
  tree.props.children[0].props.children[0].props.children[0].props.onClick();
  parts().sidebar.onSelectSession({ id: 'other-session', title: 'Other chat', messages: [] });
  callbacks.onDone('server-session', 'Deterministic file analysis');
  resolve();
  await pending;
  assert.equal(signal.aborted, true);
  assert.notEqual(parts().input.key, input.key);
  assert.deepEqual(parts().list.messages, []);
  assert.ok(writes.every(([key]) => key === 'sovereign-active-conversation:a'));
  assert.doesNotMatch(JSON.stringify(writes), /Private JSON|equipment\.json/);
});

test('file plan retains previous verified steps on a terminal failure', async () => {
  const { input } = setup();
  const pending = input.onSendTextFile('Filter and calculate a percentage', csv, rowRange);
  const { callbacks, resolve } = requests[0];
  callbacks.onPlanCreated(localFilePlan);
  callbacks.onPlanStep({ step_id: 'file_step_1', status: 'completed', summary: 'Filter matched 2 selected rows.' });
  callbacks.onPlanStep({ step_id: 'file_step_2', status: 'failed' });
  callbacks.onError('Step 2 did not complete. Last verified step: Filter matched 2 selected rows.', { code: 'TEXT_WORKFLOW_STEP_FAILED' });
  resolve();
  await pending;
  const result = parts().list.messages[1];
  assert.equal(result.fileName, csv.name);
  assert.equal(result.plan.taskId, undefined);
  assert.equal(result.plan.steps[0].summary, 'Filter matched 2 selected rows.');
  assert.equal(result.plan.steps[1].status, 'failed');
  assert.equal(result.workflowStatus, 'failed');
  assert.equal(result.error, true);
  assert.match(result.errorMessage, /Last verified step/);
});

test('completion notification alone does not mark file analysis complete', async () => {
  const { input } = setup();
  const pending = input.onSendTextFile('Filter the rows', csv, rowRange);
  const { callbacks, resolve } = requests[0];
  callbacks.onPlanCreated(localFilePlan);
  callbacks.onTaskStatus('task_completed', '');
  assert.equal(parts().list.messages[1].workflowStatus, 'executing');
  assert.equal(parts().list.messages[1].model_used, undefined);
  callbacks.onDelta('Verified result');
  callbacks.onDone('server-session', 'Deterministic file analysis');
  resolve();
  await pending;
  assert.equal(parts().list.messages[1].workflowStatus, 'completed');
  assert.equal(parts().list.messages[1].model_used, 'Deterministic file analysis');
});

test('a rejected file follow-up asks for reattachment and context without sending old bytes', async () => {
  const { input } = setup();
  const pending = input.onSendMessage('Now show only those above 80.');
  const request = requests[0];
  assert.equal(request.payload.file, undefined);
  request.callbacks.onError('Old file unavailable', { code: 'TEXT_FILE_REATTACH_REQUIRED', status: 409 });
  request.resolve();
  await pending;
  assert.match(parts().list.messages[1].errorMessage, /Reattach.*restate any previous filters/);
});

test('revocation clears active file messages, aborts the stream, and resets the composer', async (t) => {
  const previous = global.sessionStorage;
  global.sessionStorage = { removeItem() {} };
  t.after(() => { global.sessionStorage = previous; });
  authRefreshes = 0;
  const { input } = setup();
  const pending = input.onSendTextFile('Analyze the file', csv, rowRange);
  const request = requests[0];
  request.callbacks.onError('Sign in again to ask about a text file.', { code: 'TEXT_FILE_AUTH_CHANGED' });
  request.callbacks.onDelta('late secret');
  request.resolve();
  await pending;
  assert.equal(request.signal.aborted, true);
  assert.equal(authRefreshes, 1);
  assert.notEqual(parts().input.key, input.key);
  assert.deepEqual(parts().list.messages, []);
});

test('reselecting the same conversation remounts the attachment composer', () => {
  const initial = setup();
  initial.tree.props.children[0].props.children[0].props.children[0].props.onClick();
  parts().sidebar.onSelectSession({ id: 'server-session', title: 'Same conversation', messages: [] });
  assert.notEqual(parts().input.key, initial.input.key);
  assert.equal(requests.length, 0);
});

test('a file request to an unavailable conversation drops the file turn without refreshing authentication', async (t) => {
  const previous = global.sessionStorage;
  global.sessionStorage = { removeItem() {} };
  t.after(() => { global.sessionStorage = previous; });
  authRefreshes = 0;
  const { input } = setup();
  const pending = input.onSendTextFile('Analyze the file', csv, rowRange);
  const request = requests[0];
  request.callbacks.onError('Conversation not found.', { status: 404 });
  request.callbacks.onDelta('late secret');
  request.resolve();
  await pending;
  assert.equal(request.signal.aborted, true);
  assert.equal(authRefreshes, 0);
  assert.deepEqual(parts().list.messages, []);
});

test('completing file analysis persists only the conversation selection in the browser', async (t) => {
  const saved = { session: global.sessionStorage, local: global.localStorage };
  const writes = [];
  global.sessionStorage = { setItem: (...args) => writes.push(args), removeItem() {} };
  global.localStorage = { setItem: (...args) => writes.push(args) };
  t.after(() => { global.sessionStorage = saved.session; global.localStorage = saved.local; });
  const { input } = setup();
  const pending = input.onSendTextFile('Analyze private measurements', csv, rowRange);
  const { callbacks, resolve } = requests[0];
  callbacks.onPlanCreated(localFilePlan);
  callbacks.onPlanStep({ step_id: 'file_step_1', status: 'completed', summary: 'Private intermediate' });
  callbacks.onTaskStatus('task_completed', '');
  callbacks.onDelta('Private file-derived answer');
  callbacks.onDone('server-session', 'Deterministic file analysis');
  resolve();
  await pending;
  parts();
  effects[3]();
  assert.deepEqual(writes, [['sovereign-active-conversation:a', 'server-session']]);
});

test('file network errors show retry guidance without raw diagnostics', async () => {
  const { input } = setup();
  const pending = input.onSendTextFile('Filter rows', csv, rowRange);
  const { callbacks, resolve } = requests[0];
  callbacks.onError('Unexpected transport failure at private-path', { code: 'STREAM_CONNECTION_LOST' });
  resolve();
  await pending;
  const reply = parts().list.messages[1];
  assert.match(reply.errorMessage, /connection was interrupted.*Reattach/);
  assert.doesNotMatch(reply.errorMessage, /private-path/);
});

test('CSV uses the existing submission lock and drops a late answer on logout', async () => {
  const { input } = setup();
  const abortRequest = effects[1]();
  const csv = { name: 'sample.csv', type: 'text/csv', size: 18 };
  const range = { start: 2, end: 2 };
  const pending = input.onSendTextFile('What is selected?', csv, range);
  await input.onSendTextFile('Duplicate', csv, range);
  assert.equal(requests.length, 1);
  assert.equal(parts().list.messages[0].attachments[0].mimeType, 'text/csv');
  const request = requests[0];
  assert.deepEqual(request.payload.range, range);
  abortRequest();
  assert.equal(request.signal.aborted, true);
  request.callbacks.onDelta('late CSV data');
  request.callbacks.onDone('server-session', 'fake');
  request.resolve();
  await pending;
  assert.equal(parts().list.messages[1].content, '');
});

test('approval-like text with an attachment stays in image chat', async () => {
  setup();
  slots[0] = [{ id: 'approval', role: 'assistant', content: '', pendingApproval: { status: 'pending', task_id: 'task' } }];
  const releasePreviews = effects[0]();
  const image = syntheticImage();
  const pending = parts().input.onSendMessage('approve', image);
  try {
    assert.equal(requests.length, 1);
    assert.equal(requests[0].payload.image, image);
    requests[0].callbacks.onDone('server-session', 'fake');
    requests[0].resolve();
    await pending;
  } finally {
    releasePreviews();
  }
});

test('switching vision models while the draft opens discards the pending image request', async (t) => {
  const previousCreate = createImpl;
  const previousVision = visionModel;
  t.after(() => { createImpl = previousCreate; visionModel = previousVision; });
  let finishCreating;
  createImpl = () => new Promise(resolve => { finishCreating = resolve; });
  setup();
  slots[3] = '';
  const pending = parts().input.onSendMessage('Describe', syntheticImage());
  visionModel = 'ollama/other-vision:7b';
  parts();
  effects[4]();
  finishCreating({ session_id: 'new-server-session' });
  await pending;
  assert.equal(requests.length, 0);
  assert.equal(parts().input.isStreaming, false);
});

test('starting a new chat from an empty draft resets the attachment composer', () => {
  setup();
  slots[3] = '';
  const { input } = parts();
  assert.match(input.key, /^new-draft/);
  input.onClearSession();
  assert.match(parts().input.key, /^new-draft/);
  assert.notEqual(parts().input.key, input.key);
});

test('cancelling a CSV draft releases the composer before an obsolete create response', async (t) => {
  const previousCreate = createImpl;
  t.after(() => { createImpl = previousCreate; });
  const creations = [];
  createImpl = signal => new Promise(resolve => creations.push({ signal, resolve }));
  setup();
  slots[3] = '';
  const csv = { name: 'sample.csv', type: 'text/csv', size: 18 };
  const old = parts().input.onSendTextFile('Summarize the CSV', csv, { start: 1, end: 1 });
  parts().input.onStopStream();
  assert.equal(creations[0].signal.aborted, true);
  assert.equal(parts().input.isStreaming, false);

  const current = parts().input.onSendMessage('New plain message');
  assert.equal(creations.length, 2);
  creations[0].resolve({ session_id: 'obsolete' });
  await old;
  assert.equal(parts().input.isStreaming, true);
  assert.equal(slots[3], '');
  assert.equal(requests.length, 0);
  creations[1].resolve({ session_id: 'current' });
  await Promise.resolve();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].payload.session_id, 'current');
  assert.equal(requests[0].payload.message, 'New plain message');
  assert.equal(requests[0].payload.file, undefined);
  requests[0].callbacks.onDone('current', 'fake');
  requests[0].resolve();
  await current;
});

test('logout aborts a pending conversation create before any attachment submission', async (t) => {
  const previousCreate = createImpl;
  t.after(() => { createImpl = previousCreate; });
  let signal, finish;
  createImpl = value => { signal = value; return new Promise(resolve => { finish = resolve; }); };
  setup();
  slots[3] = '';
  const view = parts();
  const unmount = effects[1]();
  const pending = view.input.onSendMessage('Describe', syntheticImage());
  unmount();
  assert.equal(signal.aborted, true);
  finish({ session_id: 'obsolete' });
  await pending;
  assert.equal(requests.length, 0);
  assert.equal(slots[3], '');
});

test('changing the text model while a plain draft opens prevents dispatch on the old model', async (t) => {
  const previousCreate = createImpl;
  const previousModel = textModel;
  t.after(() => { createImpl = previousCreate; textModel = previousModel; });
  let finish;
  createImpl = () => new Promise(resolve => { finish = resolve; });
  setup();
  slots[3] = '';
  const pending = parts().input.onSendMessage('Explain pumps');
  textModel = 'ollama/new-text';
  parts();
  effects[4]();
  finish({ session_id: 'created-server-id' });
  await pending;
  assert.equal(requests.length, 0);
  assert.equal(parts().input.isStreaming, false);
});

test('stopping and switching an image turn discards late events and revokes the old preview', async () => {
  let view = setup();
  const pending = view.input.onSendMessage('Describe', syntheticImage());
  const request = requests[0];
  request.callbacks.onDelta('partial image answer');
  view = parts();
  const preview = view.list.messages[0].attachments[0].objectUrl;
  view.input.onStopStream();
  assert.equal(parts().list.messages[1].workflowStatus, 'interrupted');
  view.tree.props.children[0].props.children[0].props.children[0].props.onClick();
  parts().sidebar.onSelectSession({ id: 'other', title: 'Other', messages: [] });
  assert.equal(require('node:buffer').resolveObjectURL(preview), undefined);
  assert.equal(request.signal.aborted, true);
  request.callbacks.onDelta('late image answer');
  request.callbacks.onDone('server-session', 'fake');
  request.resolve();
  await pending;
  assert.deepEqual(parts().list.messages, []);
});

test('a failed demo image download leaves its prompt unsent', async (t) => {
  const originalWindow = global.window;
  const originalFetch = global.fetch;
  t.after(() => { global.window = originalWindow; global.fetch = originalFetch; });
  let launch;
  global.window = {
    addEventListener: (_event, handler) => { launch = handler; },
    removeEventListener() {},
  };
  global.fetch = async () => ({ ok: false });
  setup();
  const cleanup = effects[5]();
  try {
    await launch({ detail: { prompt: 'Describe this image', isMultimodal: true, imageFile: 'sample.png' } });
    assert.equal(requests.length, 0);
  } finally {
    cleanup();
  }
});

test('a demo download that finishes after a conversation switch cannot send', async (t) => {
  const originalWindow = global.window;
  const originalFetch = global.fetch;
  t.after(() => { global.window = originalWindow; global.fetch = originalFetch; });
  let launch;
  let finishBlob;
  let downloadSignal;
  global.window = {
    addEventListener: (_event, handler) => { launch = handler; },
    removeEventListener() {},
  };
  global.fetch = async (_url, options) => {
    downloadSignal = options.signal;
    return { ok: true, blob: () => new Promise(resolve => { finishBlob = resolve; }) };
  };
  const view = setup();
  const cleanup = effects[5]();
  try {
    const pending = launch({ detail: { prompt: 'Describe', isMultimodal: true, imageFile: 'sample.png' } });
    await Promise.resolve();
    view.tree.props.children[0].props.children[0].props.children[0].props.onClick();
    parts().sidebar.onSelectSession({ id: 'other', title: 'Other', messages: [] });
    finishBlob(syntheticImage());
    await pending;
    assert.equal(requests.length, 0);
  } finally {
    cleanup();
  }
  assert.equal(downloadSignal.aborted, true);
});
test('stream error interrupts active workflow and unlocks composer',async()=>{let v=setup();let pending=v.input.onSendMessage('work');let c=requests[0].callbacks;c.onTaskStatus('task_started','t');c.onToolEvent({type:'tool_start',tool:'calculator'});c.onError('Connection interrupted');requests[0].resolve();await pending;v=parts();assert.equal(v.list.messages[1].workflowStatus,'interrupted');assert.equal(v.list.messages[1].error,true);assert.equal(v.input.isStreaming,false)});
test('tool result followed by stream failure does not offer automatic retry',async()=>{let v=setup();let pending=v.input.onSendMessage('work');let c=requests[0].callbacks;c.onToolEvent({type:'tool_start',tool:'calculator'});c.onToolEvent({type:'tool_result',tool:'calculator',success:true});c.onError('Connection interrupted');requests[0].resolve();await pending;v=parts();assert.equal(v.list.messages[1].error,true);assert.equal(v.list.messages[1].toolEvents.length,2);assert.equal(v.list.onRetry,undefined);assert.equal(requests.length,1)});
test('failed and cancelled task events unlock the composer without completion',async()=>{for(const status of ['task_failed','task_cancelled']){let v=setup();let pending=v.input.onSendMessage('work');let c=requests[0].callbacks;c.onDelta('partial');c.onTaskStatus(status,'t');requests[0].resolve();await pending;v=parts();assert.equal(v.input.isStreaming,false);assert.equal(v.list.messages[1].isStreaming,false);assert.equal(v.list.messages[1].workflowStatus,status==='task_failed'?'failed':'cancelled')}});
test('image sending is blocked when selected reasoning model is unavailable',()=>{textBlocked=true;try{let v=setup();assert.equal(v.input.imageBlocked,true);assert.match(v.input.imageStatus,/selected chat model is unavailable/i)}finally{textBlocked=false}});
test('submission progressive response and completion',async()=>{let v=setup();let pending=v.input.onSendMessage('Hello');v=parts();assert.equal(v.input.isStreaming,true);assert.deepEqual(v.list.messages.map(x=>x.content),['Hello','']);requests[0].callbacks.onDelta('Hello ');requests[0].callbacks.onDelta('there');assert.equal(parts().list.messages[1].content,'Hello there');requests[0].callbacks.onDone('s','fake');requests[0].resolve();await pending;assert.equal(parts().input.isStreaming,false)});
test('switch cancels and suppresses stale response',async()=>{let v=setup();let pending=v.input.onSendMessage('Old');requests[0].callbacks.onDelta('partial');v=parts();v.tree.props.children[0].props.children[0].props.children[0].props.onClick();v=parts();v.sidebar.onSelectSession({id:'other',title:'Other',messages:[{id:'x',role:'user',content:'Other message'}]});assert.equal(requests[0].signal.aborted,true);requests[0].callbacks.onDelta('late');requests[0].callbacks.onDone('old','fake');requests[0].resolve();await pending;v=parts();assert.deepEqual(v.list.messages.map(x=>x.content),['Other message']);assert.equal(v.input.isStreaming,false)});
test('provider error remains visible without replacing the streamed partial reply', async () => {
  const view = setup();
  const pending = view.input.onSendMessage('hello');
  requests[0].callbacks.onDelta('Partial answer');
  requests[0].callbacks.onError('Selected model is unavailable');
  requests[0].resolve();
  await pending;
  const { input, list } = parts();
  const reply = list.messages[1];
  assert.equal(reply.error, true);
  assert.equal(reply.errorMessage, 'Selected model is unavailable');
  assert.equal(reply.content, 'Partial answer');
  assert.equal(reply.isStreaming, false);
  assert.equal(input.isStreaming, false);
  assert.equal(list.onRetry, undefined);
});
test('stop control aborts generation and keeps partial response',async()=>{let v=setup();let pending=v.input.onSendMessage('Hello');requests[0].callbacks.onDelta('partial');parts().input.onStopStream();assert.equal(requests[0].signal.aborted,true);requests[0].callbacks.onDelta('late');requests[0].resolve();await pending;v=parts();assert.equal(v.input.isStreaming,false);assert.equal(v.list.messages[1].content,'partial');assert.equal(v.list.messages[1].isStreaming,false)});
test('workflow progress stays active until final stream completion',async()=>{let v=setup();let pending=v.input.onSendMessage('calculate');let c=requests[0].callbacks;c.onPlanCreated({task_id:'task1',plan:{objective:'Calculate',steps:[{id:'step_1',description:'Calculate',status:'pending'}]}});c.onPlanStep({step_id:'step_1',status:'running'});assert.equal(parts().list.messages[1].plan.steps[0].status,'running');c.onPlanStep({step_id:'step_1',status:'completed'});c.onTaskStatus('task_completed','task1');assert.notEqual(parts().list.messages[1].workflowStatus,'completed');assert.equal(parts().input.isStreaming,true);c.onDelta('Result: 42');c.onDone('s','fake');requests[0].resolve();await pending;v=parts();assert.equal(v.input.isStreaming,false);assert.equal(v.list.messages[1].content,'Result: 42');assert.equal(v.list.messages[1].plan.steps[0].status,'completed')});
test('approval followed by task failure terminates without claiming success',async()=>{let v=setup();let pending=v.input.onSendMessage('write');let c=requests[0].callbacks;c.onApprovalRequired({task_id:'task1',approval_id:'ap',step_id:'step_1',tool_name:'synthetic_write'});v=parts();assert.equal(v.list.messages[1].pendingApproval.status,'pending');assert.doesNotMatch(v.list.messages[1].content,/completed|success/i);c.onTaskStatus('task_failed','task1');requests[0].resolve();await pending;v=parts();assert.equal(v.list.messages[1].workflowStatus,'failed');assert.equal(v.list.messages[1].isStreaming,false);assert.equal(v.input.isStreaming,false);assert.doesNotMatch(v.list.messages[1].content,/completed|success/i)});
test('late workflow events ignored after cancellation',async()=>{let v=setup();let pending=v.input.onSendMessage('work');let c=requests[0].callbacks;parts().input.onStopStream();c.onPlanCreated({task_id:'late',plan:{steps:[]}});c.onTaskStatus('task_completed','late');c.onDelta('false success');requests[0].resolve();await pending;v=parts();assert.equal(v.list.messages[1].content,'');assert.equal(v.list.messages[1].plan,undefined)});
test('workflow status distinguishes planning, execution, approval and failure',async()=>{let v=setup();let pending=v.input.onSendMessage('work');let c=requests[0].callbacks;c.onTaskStatus('task_started','t');assert.equal(parts().list.messages[1].workflowStatus,'planning');c.onPlanCreated({task_id:'t',plan:{steps:[{id:'step_1',status:'pending'}]}});assert.equal(parts().list.messages[1].workflowStatus,'executing');c.onApprovalRequired({task_id:'t',approval_id:'a',step_id:'step_1',tool_name:'synthetic'});assert.equal(parts().list.messages[1].workflowStatus,'awaiting_approval');c.onTaskStatus('task_failed','t');assert.equal(parts().list.messages[1].workflowStatus,'failed');requests[0].resolve();await pending;v=parts();assert.equal(v.list.messages[1].workflowStatus,'failed');assert.equal(v.list.messages[1].isStreaming,false);assert.equal(v.input.isStreaming,false)});
test('interrupted workflow warns without fabricated tool activity',async()=>{let v=setup();let pending=v.input.onSendMessage('work');let c=requests[0].callbacks;c.onAgentStatus('planning');v=parts();assert.equal(v.list.messages[1].workflowStatus,'planning');assert.equal(v.list.messages[1].toolEvents.length,0);v.input.onStopStream();assert.equal(parts().list.messages[1].workflowStatus,'interrupted');requests[0].resolve();await pending});
test('verified unit conversion result and workflow failure are shown separately',async()=>{let v=setup();let pending=v.input.onSendMessage('Convert 2 km to metres');let c=requests[0].callbacks;c.onTaskStatus('task_started','t');c.onToolEvent({type:'tool_start',tool:'unit_conversion'});c.onToolEvent({type:'tool_result',tool:'unit_conversion',success:true,summary:'Result: 2000.0 m'});c.onTaskStatus('task_completed','t');c.onDelta('Result: 2000.0 m');c.onDone('s','fake');requests[0].resolve();await pending;v=parts();assert.equal(v.list.messages[1].toolEvents[1].summary,'Result: 2000.0 m');assert.equal(v.list.messages[1].workflowStatus,'completed');assert.equal(v.list.messages[1].content,'Result: 2000.0 m')});

test('new draft obtains a server ID before sending',async()=>{let v=setup();slots[3]='';v=parts();let pending=v.input.onSendMessage('hello');await Promise.resolve();assert.equal(requests[0].payload.session_id,'created-server-id');requests[0].callbacks.onDone('created-server-id','fake');requests[0].resolve();await pending;assert.equal(slots[3],'created-server-id')});
test('a stale create response cannot switch the active conversation',async()=>{let resolveCreate;createImpl=()=>new Promise(r=>resolveCreate=r);let v=setup();slots[3]='';v=parts();let pending=v.input.onSendMessage('hello');v=parts();v.tree.props.children[0].props.children[0].props.children[1].props.onClick();resolveCreate({session_id:'stale'});await pending;assert.equal(requests.length,0);assert.equal(slots[3],'');createImpl=async()=>({session_id:'created-server-id'})});

function localHarness(t) {
  const oldHistory = historyImpl;
  const oldWindow = global.window;
  const oldStorage = global.sessionStorage;
  localMode = true;
  t.after(() => {
    localMode = false;
    localOffline = false;
    historyImpl = oldHistory;
    global.window = oldWindow;
    global.sessionStorage = oldStorage;
    delete api.cancelLocalTurn;
  });
  return setup();
}

function snapshot(userId, state, attempt=1) {
  const user = { id: userId, role:'user', content:'Remember Cedar.', timestamp:'',
    localTurn:{ attempt, state, model:textModel } };
  return { id:'server-session', title:'Conversation', messages: state === 'completed' ?
    [user, {id:'reply-from-server', role:'assistant', content:'Cedar.', timestamp:''}] : [user] };
}

function buttonWithText(tree, label) {
  if (Array.isArray(tree)) return tree.map(child => buttonWithText(child, label)).find(Boolean);
  if (!tree || typeof tree !== 'object') return;
  if (tree.type === 'button' && tree.props.children.includes(label)) return tree;
  return buttonWithText(tree.props?.children, label);
}

test('local stream with lost completion displays only the reconciled server reply', async (t) => {
  const view = localHarness(t);
  const finishing = view.input.onSendMessage('Remember Cedar.');
  const request = requests[0];
  assert.match(request.payload.turn_id, /^[0-9a-f-]{36}$/);
  assert.equal(request.payload.attempt, 1);
  assert.equal(request.payload.tools_enabled, false);
  request.callbacks.onAccepted();
  request.callbacks.onDelta('Provisional output.');
  assert.equal(parts().list.messages.at(-1).localPreview, true);
  localOffline = true;
  assert.equal(parts().input.textOnly, true);
  historyImpl = async () => snapshot(request.payload.turn_id, 'completed');
  request.callbacks.onError('Connection interrupted');
  request.resolve();
  await finishing;
  assert.deepEqual(parts().list.messages.map(message => message.id), [request.payload.turn_id, 'reply-from-server']);
  assert.equal(parts().list.messages.at(-1).content, 'Cedar.');
  assert.equal(requests.length, 1);
});

test('refresh during a local stream polls history without posting the turn', async (t) => {
  localHarness(t);
  const foreignPreview = 'turn-already-admitted';
  global.sessionStorage = { getItem: () => 'server-session', setItem() {}, removeItem() {} };
  historyImpl = async () => snapshot(foreignPreview, 'running');
  let poll;
  global.window = { addEventListener() {}, removeEventListener() {},
    setInterval(callback) { poll = callback; return 9; }, clearInterval() {} };
  parts();
  const endRestore = effects[2]();
  await new Promise(setImmediate);
  assert.equal(parts().input.isStreaming, true);
  const stopPolling = effects.at(-1)();
  historyImpl = async () => snapshot(foreignPreview, 'completed');
  poll();
  await new Promise(setImmediate);
  assert.equal(parts().input.isStreaming, false);
  assert.equal(parts().list.messages.at(-1).id, 'reply-from-server');
  assert.equal(requests.length, 0);
  stopPolling();
  endRestore();
});

test('local stop targets the admitted attempt and retries only with the next attempt number', async (t) => {
  const view = localHarness(t);
  const finishing = view.input.onSendMessage('Remember Cedar.');
  const first = requests[0];
  assert.equal(parts().input.stopDisabled, true);
  first.callbacks.onAccepted();
  assert.equal(parts().input.stopDisabled, false);
  let cancelled;
  api.cancelLocalTurn = async (...args) => {
    cancelled = args;
    historyImpl = async () => snapshot(first.payload.turn_id, 'cancelled');
  };
  parts().input.onStopStream();
  await new Promise(setImmediate);
  assert.deepEqual(cancelled, ['server-session', first.payload.turn_id, 1]);
  assert.equal(first.signal.aborted, true);
  first.callbacks.onDelta('Late output cannot appear');
  first.resolve();
  await finishing;
  assert.equal(parts().list.messages.length, 1);
  const retry = buttonWithText(parts().tree, 'Retry response');
  retry.props.onClick();
  assert.equal(requests[1].payload.turn_id, first.payload.turn_id);
  assert.equal(requests[1].payload.attempt, 2);
  historyImpl = async () => snapshot(first.payload.turn_id, 'completed', 2);
  requests[1].resolve();
  await new Promise(setImmediate);
  assert.deepEqual(parts().list.messages.map(message => message.id), [first.payload.turn_id, 'reply-from-server']);
});

test('ambiguous submission exposes an explicit resend of the same attempt', async (t) => {
  const view = localHarness(t);
  const firstSend = view.input.onSendMessage('Remember Cedar.');
  const first = requests[0];
  historyImpl = async () => ({id:'server-session', title:'Conversation', messages:[]});
  first.resolve();
  await firstSend;
  assert.equal(parts().input.sendBlocked, true);
  buttonWithText(parts().tree, 'Resend same attempt').props.onClick();
  assert.deepEqual(requests[1].payload, first.payload);
  historyImpl = async () => snapshot(first.payload.turn_id, 'completed');
  requests[1].resolve();
  await new Promise(setImmediate);
  assert.equal(parts().list.messages.length, 2);
});
test('confirmed deletion of active chat aborts and ignores stale stream',async()=>{let v=setup();let pending=v.input.onSendMessage('Old');requests[0].callbacks.onDelta('partial');v=parts();v.tree.props.children[0].props.children[0].props.children[0].props.onClick();v=parts();v.sidebar.onNewChat();assert.equal(requests[0].signal.aborted,true);requests[0].callbacks.onDelta('late');requests[0].resolve();await pending;v=parts();assert.deepEqual(v.list.messages,[]);assert.equal(v.input.isStreaming,false)});

test('approval event keeps composer locked until stream terminal event',async()=>{let v=setup();let pending=v.input.onSendMessage('work');let c=requests[0].callbacks;c.onApprovalRequired({task_id:'t',approval_id:'a',step_id:'s',tool_name:'write'});v=parts();assert.equal(v.input.isStreaming,true);assert.equal(v.list.messages[1].pendingApproval.status,'pending');await v.input.onSendMessage('another message');assert.equal(requests.length,1);c.onDone('server-session','fake');requests[0].resolve();await pending;v=parts();assert.equal(v.input.isStreaming,false);assert.equal(v.list.messages[1].workflowStatus,'awaiting_approval')});
