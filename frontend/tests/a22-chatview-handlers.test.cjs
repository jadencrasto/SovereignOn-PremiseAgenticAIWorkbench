// Deterministic hook harness exercising production ChatView handlers, not React DOM.
const {test}=require('node:test'), assert=require('node:assert/strict'), fs=require('fs'), path=require('path'), Module=require('module'), ts=require('typescript');
let slots, cursor, requests, effects, textBlocked=false, textModel='fake';
const React={createElement:(type,props,...children)=>({type,props:{...props,children}}),useState(initial){let i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v}]},useRef(initial){let i=cursor++;return slots[i]??=( {current:initial} )},useCallback(f){cursor++;return f},useEffect(effect){cursor++;effects.push(effect)}};
function MessageList(){} function ChatInput(){} function ChatHistorySidebar(){}
let createImpl=async()=>({session_id:'created-server-id'});
let visionModel = 'ollama/vision:7b';
const api={createSession:signal=>createImpl(signal),fetchSessions:async()=>({count:0}),streamChat(payload,callbacks,signal){let resolve;let p=new Promise(r=>resolve=r);requests.push({payload,callbacks,signal,resolve});return p},guardChatCallbacks(callbacks,active){return new Proxy(callbacks,{get:(t,p)=>(...a)=>{if(active())t[p](...a)}})}};
api.streamChatMultimodal = api.streamChat;
api.streamChatTextFile = (file, session_id, model, message, callbacks, signal, range) =>
  api.streamChat({ file, session_id, model, message, range }, callbacks, signal);
const chatFile=path.resolve(__dirname,'../src/api/chat.ts');
const chatOut=ts.transpileModule(fs.readFileSync(chatFile,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const chatMod=new Module(chatFile,module);chatMod.filename=chatFile;chatMod.paths=Module._nodeModulePaths(path.dirname(chatFile));const chatRequire=chatMod.require.bind(chatMod);chatMod.require=n=>n==='./client'?{}:chatRequire(n);chatMod._compile(chatOut,chatFile);
api.guardChatCallbacks=chatMod.exports.guardChatCallbacks;
const mocks={react:React,'./MessageList':{MessageList},'./ChatInput':{ChatInput},'./ChatHistorySidebar':{ChatHistorySidebar,loadSession:async()=>({id:'server-session',title:'Conversation',messages:[]})},'../../context/AuthContext':{useAuth:()=>({user:{id:'a'}})},'../../api/chat':api,'../../context/WorkbenchContext':{useWorkbench:()=>({selectedModel:textModel,selectedVisionModel:visionModel,addToast(){},capabilities:{text_file_understanding:true},isCheckingHealth:false})},'../../api/capabilities':{capabilityFor:()=>({state:'available'}),chatDisabled:(_report,image)=>image?false:textBlocked,describeCapability:()=>'',permitsCapability:()=>false},'../../api/tasks':{},'lucide-react':{Clock:()=>null,Plus:()=>null}};
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
  effects.at(-2)();
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
  effects.at(-2)();
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
  const cleanup = effects.at(-1)();
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
  const cleanup = effects.at(-1)();
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
test('confirmed deletion of active chat aborts and ignores stale stream',async()=>{let v=setup();let pending=v.input.onSendMessage('Old');requests[0].callbacks.onDelta('partial');v=parts();v.tree.props.children[0].props.children[0].props.children[0].props.onClick();v=parts();v.sidebar.onNewChat();assert.equal(requests[0].signal.aborted,true);requests[0].callbacks.onDelta('late');requests[0].resolve();await pending;v=parts();assert.deepEqual(v.list.messages,[]);assert.equal(v.input.isStreaming,false)});

test('approval event keeps composer locked until stream terminal event',async()=>{let v=setup();let pending=v.input.onSendMessage('work');let c=requests[0].callbacks;c.onApprovalRequired({task_id:'t',approval_id:'a',step_id:'s',tool_name:'write'});v=parts();assert.equal(v.input.isStreaming,true);assert.equal(v.list.messages[1].pendingApproval.status,'pending');await v.input.onSendMessage('another message');assert.equal(requests.length,1);c.onDone('server-session','fake');requests[0].resolve();await pending;v=parts();assert.equal(v.input.isStreaming,false);assert.equal(v.list.messages[1].workflowStatus,'awaiting_approval')});
