const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
let slots,cursor,sent,revoked,effects,preview,previewCsv;
const R={createElement:(type,props,...children)=>({type,props:{...props,children}}),useState(v){let i=cursor++;if(!(i in slots))slots[i]=v;return [slots[i],x=>slots[i]=x]},useRef(v){let i=cursor++;return slots[i]??={current:v}},useCallback:f=>f,useEffect(fn){effects.push(fn)}};
const f=path.resolve(__dirname,'../src/components/chat/ChatInput.tsx'),m=new Module(f,module);m.filename=f;m.paths=Module._nodeModulePaths(path.dirname(f));let orig=m.require.bind(m);m.require=n=>n==='react'?R:n==='lucide-react'?new Proxy({},{get:(_,p)=>p}):n==='../../types'?{ALLOWED_IMAGE_TYPES:['image/png','image/jpeg','image/webp'],MAX_IMAGE_SIZE_BYTES:10*1024*1024}:n==='../../api/chat'?{previewTextFile:(...args)=>preview(...args),previewCsvFile:(...args)=>previewCsv(...args)}:orig(n);m._compile(ts.transpileModule(fs.readFileSync(f,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,f);
URL.createObjectURL=()=> 'blob:synthetic';URL.revokeObjectURL=x=>revoked.push(x);
function render(p={}){cursor=0;effects=[];return m.exports.ChatInput({onSendMessage:(...a)=>sent.push(a),onStopStream:()=>{},onClearSession:()=>{},isStreaming:false,sendBlocked:false,imageBlocked:false,...p})}
function find(n,fn){if(!n||typeof n!=='object')return null;if(fn(n))return n;for(const c of (Array.isArray(n)?n:n.props?.children||[])){const found=find(c,fn);if(found)return found}return null}
function setup(){slots=[];sent=[];revoked=[];preview=previewCsv=async()=>{throw Error('Unexpected preview')};return render()}
test('image selection and submission preserve the selected file',()=>{let t=setup(),f={name:'synthetic.png',type:'image/png',size:5};find(t,n=>n.props?.id==='image-file-input').props.onChange({target:{files:[f],value:'x'}});t=render();assert.equal(find(t,n=>n.type==='img').props.src,'blob:synthetic');find(t,n=>n.type==='textarea').props.onChange({target:{value:'Describe',style:{},scrollHeight:5}});t=render();find(t,n=>n.props?.id==='send-message-btn').props.onClick();assert.deepEqual(sent,[['Describe',f]]);assert.deepEqual(revoked,['blob:synthetic'])});
test('unsupported attachment shows error and blocked capability disables selection',()=>{let t=setup();find(t,n=>n.props?.id==='image-file-input').props.onChange({target:{files:[{type:'application/pdf',size:5}],value:'x'}});t=render();assert.ok(find(t,n=>n.type==='span'&&String(n.props.children).includes('INVALID FORMAT')));t=render({imageBlocked:true,imageStatus:'Vision unavailable'});assert.equal(find(t,n=>n.type==='button'&&n.props.title==='Vision unavailable').props.disabled,true)});
test('streaming displays cancellation control',()=>{setup();let t=render({isStreaming:true});assert.ok(find(t,n=>n.props?.id==='stop-stream-btn'));assert.equal(find(t,n=>n.type==='textarea').props.disabled,true)});
test('invalid replacement clears prior attachment',()=>{let t=setup(),f={name:'ok.png',type:'image/png',size:5};find(t,n=>n.props?.id==='image-file-input').props.onChange({target:{files:[f],value:'x'}});t=render();find(t,n=>n.props?.id==='image-file-input').props.onChange({target:{files:[{type:'application/pdf',size:5}],value:'x'}});t=render();assert.equal(find(t,n=>n.type==='img'),null);assert.deepEqual(revoked,['blob:synthetic'])});
test('removing an image revokes the preview and preserves ordinary text sending', () => {
  let tree = setup();
  const image = { name: 'sample.webp', type: 'image/webp', size: 8 };
  find(tree, n => n.props?.id === 'image-file-input').props.onChange({ target: { files: [image], value: '' } });
  tree = render();
  find(tree, n => n.type === 'button' && n.props.children?.includes('Remove')).props.onClick();
  tree = render({ imageBlocked: true });
  assert.equal(find(tree, n => n.type === 'img'), null);
  assert.deepEqual(revoked, ['blob:synthetic']);
  find(tree, n => n.type === 'textarea').props.onChange({ target: { value: 'Text only' } });
  tree = render({ imageBlocked: true });
  find(tree, n => n.props?.id === 'send-message-btn').props.onClick();
  assert.deepEqual(sent, [['Text only', undefined]]);
});

test('the composer explains that an earlier image needs to be reattached', () => {
  setup();
  let tree = render({ hasEarlierImage: true });
  const guidance = node => node.type === 'p' && String(node.props.children).includes('Reattach the image');
  assert.ok(find(tree, guidance));
  const image = { name: 'sample.png', type: 'image/png', size: 8 };
  find(tree, node => node.props?.id === 'image-file-input').props.onChange({ target: { files: [image], value: '' } });
  tree = render({ hasEarlierImage: true });
  assert.equal(find(tree, guidance), null);
});

const textFile = { name: 'sample.txt', type: 'text/plain', size: 24 };
const selectText = (tree, file = textFile) => find(tree, n => n.props?.id === 'text-file-input')
  .props.onChange({ target: { files: [file], value: 'selection' } });
const send = tree => find(tree, n => n.props?.id === 'send-message-btn');
const question = tree => find(tree, n => n.type === 'textarea')
  .props.onChange({ target: { value: 'What color?', style: {}, scrollHeight: 1 } });
const visiblePreview = tree => find(tree, n => n.type === 'pre' && n.props['aria-label'] === 'Text file preview');

test('text preview is inert, size-labelled and independent of inference readiness', async () => {
  setup();
  const mockData = { filename: textFile.name, size_bytes: textFile.size, text: '<script>inert</script>', truncated: true };
  preview = async (file, session, signal) => {
    assert.equal(file, textFile);
    assert.equal(session, 'owned');
    assert.equal(signal.aborted, false);
    return mockData;
  };
  const options = { sendBlocked: true, sessionId: 'owned', onSendTextFile: (...args) => sent.push(args) };
  await selectText(render(options));
  const tree = render(options);
  assert.deepEqual(visiblePreview(tree).props.children, ['<script>inert</script>']);
  assert.equal(find(tree, n => n.type === 'script'), null);
  assert.ok(find(tree, n => n.props?.role === 'status' && String(n.props.children).includes('Preview shortened')));
  question(tree);
  assert.equal(send(render(options)).props.disabled, true);
  assert.deepEqual(sent, []);
  const ready = render({ ...options, sendBlocked: false });
  assert.equal(send(ready).props.disabled, false);
  send(ready).props.onClick();
  assert.deepEqual(sent, [['What color?', textFile]]);
  assert.equal(visiblePreview(render(options)), null);
});

const previewLines = tree => find(tree, node => node.type === 'button' && node.props.children.includes('Preview lines'));
const lineField = (tree, label) => find(find(tree, node => node.type === 'label' && node.props.children.includes(label)),
  node => node.type === 'input');
const setLine = (tree, label, value) => lineField(tree, label).props.onChange({ target: { value } });

const csvFile = { name: 'sample.csv', type: 'text/csv', size: 44 };
const csvTable = {
  filename: csvFile.name, size_bytes: csvFile.size, headers: ['Label', 'Value'], column_types: ['Text', 'Text'],
  row_count: 13, rows: [['<script>inert</script>', '=1+1'], ['label', '']], truncated: true,
};
const visibleCsv = tree => find(tree, n => n.type === 'table' && n.props['aria-label'] === 'CSV preview');

test('CSV preview keeps cells inert and requires a valid explicit row preview and text readiness', async () => {
  setup();
  const requests = [];
  previewCsv = async (file, session, signal, range) => {
    assert.equal(file, csvFile);
    assert.equal(session, 'owned');
    assert.equal(signal.aborted, false);
    requests.push(range);
    return range ? { ...csvTable, rows: csvTable.rows.slice(0, 1), truncated: false } : csvTable;
  };
  const options = { sessionId: 'owned', sendBlocked: true, onSendTextFile: (...args) => sent.push(args) };
  await selectText(render(options), csvFile);
  const table = visibleCsv(render(options));
  assert.ok(table);
  assert.equal(find(table, n => n.type === 'script' || n.props?.dangerouslySetInnerHTML), null);
  for (const value of ['<script>inert</script>', '=1+1', '']) {
    assert.ok(find(table, n => n.type === 'td' && n.props.children.includes(value)));
  }
  question(render(options));
  const ready = { ...options, sendBlocked: false };
  assert.equal(send(render(ready)).props.disabled, true);
  setLine(render(ready), 'From row', '1');
  setLine(render(ready), 'To row', '13');
  const rowsButton = tree => find(tree, n => n.type === 'button' && n.props.children.includes('Preview rows'));
  assert.equal(rowsButton(render(ready)).props.disabled, true);
  setLine(render(ready), 'To row', '1');
  await rowsButton(render(ready)).props.onClick();
  assert.equal(send(render(options)).props.disabled, true);
  assert.equal(send(render(ready)).props.disabled, false);
  send(render(ready)).props.onClick();
  assert.deepEqual(sent, [['What color?', csvFile, { start: 1, end: 1 }]]);
  assert.deepEqual(requests, [undefined, { start: 1, end: 1 }]);
  assert.equal(visibleCsv(render(ready)), null);
});

test('CSV errors, removal and composer unmount cannot restore stale attachment data', async () => {
  setup();
  previewCsv = async () => { throw { status: 422 }; };
  await selectText(render(), csvFile);
  const alert = find(render(), n => n.props?.role === 'alert');
  assert.ok(find(alert, n => n.type === 'span' && String(n.props.children).includes('CSV preview failed')));
  const remove = tree => find(tree, n => n.type === 'button' && n.props.children.includes('Remove CSV'));
  remove(render()).props.onClick();
  let signal, complete;
  previewCsv = (_file, _session, value) => {
    signal = value;
    return new Promise(resolve => { complete = resolve; });
  };
  const tree = render({ sessionId: 'owned' });
  const unmount = effects[0]();
  const pending = selectText(tree, csvFile);
  unmount();
  assert.equal(signal.aborted, true);
  complete(csvTable);
  await pending;
  assert.equal(visibleCsv(render()), null);
});

test('replacing TXT with CSV and then an image never sends either old file', async () => {
  setup();
  let txtSignal, csvSignal, finishTxt, finishCsv;
  preview = (_file, _session, signal) => {
    txtSignal = signal;
    return new Promise(resolve => { finishTxt = resolve; });
  };
  previewCsv = (_file, _session, signal) => {
    csvSignal = signal;
    return new Promise(resolve => { finishCsv = resolve; });
  };
  const options = { sessionId: 'owned', onSendTextFile: (...args) => sent.push(args) };
  const oldTxt = selectText(render(options));
  const oldCsv = selectText(render(options), csvFile);
  assert.equal(txtSignal.aborted, true);
  const image = { name: 'sample.png', type: 'image/png', size: 12 };
  find(render(options), n => n.props?.id === 'image-file-input')
    .props.onChange({ target: { files: [image], value: '' } });
  assert.equal(csvSignal.aborted, true);
  finishCsv(csvTable);
  finishTxt({ filename: textFile.name, size_bytes: textFile.size, text: 'stale text', line_count: 1, truncated: false });
  await Promise.all([oldTxt, oldCsv]);
  const current = render(options);
  assert.equal(visibleCsv(current), null);
  assert.equal(visiblePreview(current), null);
  assert.ok(find(current, n => n.type === 'img'));
  question(current);
  send(render(options)).props.onClick();
  assert.deepEqual(sent, [['What color?', image]]);
});

test('only a successfully previewed line range can be sent with a text question', async () => {
  setup();
  const calls = [];
  preview = async (file, session, signal, range) => {
    calls.push({ file, session, range });
    assert.equal(signal.aborted, false);
    return { filename: file.name, size_bytes: file.size, line_count: 3, truncated: false,
      text: range ? 'Amber switch' : 'First\nAmber switch\nThird' };
  };
  const options = { sessionId: 'owned', onSendTextFile: (...args) => sent.push(args) };
  await selectText(render(options));
  question(render(options));
  setLine(render(options), 'From line', '2');
  assert.equal(previewLines(render(options)).props.disabled, true);
  assert.equal(send(render(options)).props.disabled, true);
  setLine(render(options), 'To line', '2');
  const pending = previewLines(render(options)).props.onClick();
  assert.equal(send(render(options)).props.disabled, true);
  await pending;
  assert.deepEqual(visiblePreview(render(options)).props.children, ['Amber switch']);
  assert.equal(send(render(options)).props.disabled, false);
  setLine(render(options), 'To line', '3');
  send(render(options)).props.onClick();
  assert.deepEqual(sent, []);
  setLine(render(options), 'To line', '2');
  send(render(options)).props.onClick();
  assert.deepEqual(sent, [['What color?', textFile, { start: 2, end: 2 }]]);
  assert.deepEqual(calls, [{ file: textFile, session: 'owned', range: undefined },
    { file: textFile, session: 'owned', range: { start: 2, end: 2 } }]);
  assert.equal(visiblePreview(render(options)), null);
});

test('a failed range preview can be reset and a removed file discards a later preview result', async () => {
  setup();
  let lateResult;
  let lateSignal;
  const whole = { filename: textFile.name, size_bytes: textFile.size,
    line_count: 3, truncated: false, text: 'First\n\nThird' };
  preview = async (_file, _session, signal, range) => {
    if (!range) return whole;
    if (range.start === 2) throw { status: 422 };
    lateSignal = signal;
    return new Promise(resolve => { lateResult = resolve; });
  };
  const options = { onSendTextFile: (...args) => sent.push(args) };
  await selectText(render(options));
  question(render(options));
  setLine(render(options), 'From line', '2');
  setLine(render(options), 'To line', '2');
  await previewLines(render(options)).props.onClick();
  assert.ok(find(render(options), n => n.props?.role === 'alert'));
  assert.equal(visiblePreview(render(options)), null);
  assert.equal(send(render(options)).props.disabled, true);
  await find(render(options), n => n.type === 'button' && n.props.children.includes('Use whole file')).props.onClick();
  assert.deepEqual(visiblePreview(render(options)).props.children, [whole.text]);
  assert.equal(send(render(options)).props.disabled, false);
  setLine(render(options), 'From line', '3');
  setLine(render(options), 'To line', '3');
  const pending = previewLines(render(options)).props.onClick();
  find(render(options), n => n.type === 'button' && n.props.children.includes('Remove text file')).props.onClick();
  assert.equal(lateSignal.aborted, true);
  lateResult({ ...whole, text: 'Third' });
  await pending;
  assert.equal(visiblePreview(render(options)), null);
  send(render(options)).props.onClick();
  assert.deepEqual(sent, [['What color?', undefined]]);
});

test('failed preview waits for explicit removal before allowing text-only send', async () => {
  setup();
  preview = async () => { throw { status: 422 }; };
  const options = { onSendTextFile: (...args) => sent.push(args) };
  await selectText(render(options));
  let tree = render(options);
  assert.ok(find(tree, n => n.props?.role === 'alert'));
  question(tree);
  tree = render(options);
  assert.equal(send(tree).props.disabled, true);
  find(tree, n => n.type === 'button' && n.props.children.includes('Remove text file')).props.onClick();
  send(render(options)).props.onClick();
  assert.deepEqual(sent, [['What color?', undefined]]);
});

test('invalid .txt selection makes no upload and selecting an image cancels an active preview', async () => {
  setup();
  let calls = 0;
  let complete;
  let signal;
  preview = (_file, _session, value) => {
    calls++;
    signal = value;
    return new Promise(resolve => { complete = resolve; });
  };
  await selectText(render(), { ...textFile, name: 'report.pdf' });
  assert.equal(calls, 0);
  assert.ok(find(render(), n => n.props?.role === 'alert'));
  const pending = selectText(render());
  assert.equal(calls, 1);
  assert.ok(find(render(), n => n.props?.role === 'status' && n.props.children.includes('Checking text file…')));
  const image = { name: 'sample.png', type: 'image/png', size: 12 };
  find(render(), n => n.props?.id === 'image-file-input').props.onChange({ target: { files: [image], value: '' } });
  assert.equal(signal.aborted, true);
  complete({ filename: 'stale.txt', text: 'stale', size_bytes: 1, truncated: false });
  await pending;
  assert.equal(visiblePreview(render()), null);
  assert.ok(find(render(), n => n.type === 'img'));
});

test('unmount aborts an in-flight preview and drops a late result', async () => {
  setup();
  let complete;
  let signal;
  preview = (_file, _session, value) => {
    signal = value;
    return new Promise(resolve => { complete = resolve; });
  };
  const tree = render({ sessionId: 'closed-conversation' });
  const unmount = effects[0]();
  const pending = selectText(tree);
  unmount();
  assert.equal(signal.aborted, true);
  complete({ filename: 'stale.txt', text: 'stale', size_bytes: 1, truncated: false });
  await pending;
  setup();
  assert.equal(visiblePreview(render({ sessionId: 'next-conversation' })), null);
});
