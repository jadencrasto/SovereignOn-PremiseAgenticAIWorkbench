const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
let slots,cursor,sent,revoked,effects,preview,previewCsv,previewJson;
const R={createElement:(type,props,...children)=>({type,props:{...props,children}}),useState(v){let i=cursor++;if(!(i in slots))slots[i]=v;return [slots[i],x=>slots[i]=x]},useRef(v){let i=cursor++;return slots[i]??={current:v}},useCallback:f=>f,useEffect(fn){effects.push(fn)}};
const f=path.resolve(__dirname,'../src/components/chat/ChatInput.tsx'),m=new Module(f,module);m.filename=f;m.paths=Module._nodeModulePaths(path.dirname(f));let orig=m.require.bind(m);m.require=n=>n==='react'?R:n==='lucide-react'?new Proxy({},{get:(_,p)=>p}):n==='../../types'?{ALLOWED_IMAGE_TYPES:['image/png','image/jpeg','image/webp'],MAX_IMAGE_SIZE_BYTES:10*1024*1024}:n==='../../api/chat'?{MAX_TEXT_ATTACHMENTS:3,MAX_TEXT_ATTACHMENT_BYTES:2048,MAX_TEXT_ATTACHMENT_TOTAL_BYTES:4096,previewTextFile:(...args)=>preview(...args),previewCsvFile:(...args)=>previewCsv(...args),previewJsonFile:(...args)=>previewJson(...args)}:orig(n);m._compile(ts.transpileModule(fs.readFileSync(f,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,f);
URL.createObjectURL=()=> 'blob:synthetic';URL.revokeObjectURL=x=>revoked.push(x);
function render(p={}){cursor=0;effects=[];return m.exports.ChatInput({onSendMessage:(...a)=>sent.push(a),onStopStream:()=>{},onClearSession:()=>{},isStreaming:false,sendBlocked:false,imageBlocked:false,textFileSupported:true,...p})}
function find(n,fn){if(!n||typeof n!=='object')return null;if(fn(n))return n;for(const c of (Array.isArray(n)?n:n.props?.children||[])){const found=find(c,fn);if(found)return found}return null}
function setup(){slots=[];sent=[];revoked=[];preview=previewCsv=previewJson=async()=>{throw Error('Unexpected preview')};return render()}
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

test('local composer previews supported images within its 2 MiB limit', () => {
  setup();
  const props = {textOnly: true, localImage: true};
  const choose = file => find(render(props), n => n.props?.id === 'image-file-input')
    .props.onChange({target: {files: [file], value: 'selection'}});
  choose({name: 'large.png', type: 'image/png', size: 2 * 1024 * 1024 + 1});
  assert.ok(find(render(props), n => String(n.props?.children).includes('exceeds the 2 MB limit')));
  const file = {name: 'valid.png', type: 'image/png', size: 512};
  choose(file);
  assert.ok(find(render(props), n => n.type === 'img'));
  question(render(props));
  send(render(props)).props.onClick();
  assert.deepEqual(sent, [['What color?', file]]);
});

const selectBatch = (tree, files) => find(tree, n => n.props?.id === 'text-file-input')
  .props.onChange({ target: { files, value: 'selection' } });

test('multi-file picker requires an explicit source and distinguishes duplicate filenames', async () => {
  setup();
  const a = { ...textFile };
  const b = { ...textFile };
  const options = { onSendTextFiles: (...args) => sent.push(args) };
  const pick = render(options);
  assert.equal(find(pick, n => n.props?.id === 'text-file-input').props.multiple, true);
  selectBatch(pick, [a, b]);
  let tree = render(options);
  assert.ok(find(tree, n => n.type === 'span' && String(n.props.children).includes('Source ,1, · ,sample.txt')));
  assert.ok(find(tree, n => n.type === 'span' && String(n.props.children).includes('Source ,2, · ,sample.txt')));
  question(tree);
  assert.equal(send(render(options)).props.disabled, true);
  preview = async file => {
    assert.equal(file, b);
    return { filename: b.name, size_bytes: b.size, text: 'second only', line_count: 1, truncated: false };
  };
  const second = tree => find(find(tree, n => n.type === 'li' && find(n, el => el.props?.['aria-label'] === 'Remove source 2')),
    n => n.props?.type === 'radio');
  await second(render(options)).props.onChange();
  tree = render(options);
  assert.equal(second(tree).props.checked, true);
  assert.equal(send(tree).props.disabled, false);
  send(tree).props.onClick();
  assert.deepEqual(sent, [['What color?', { files: [a, b], source: 2 }, undefined]]);
  assert.equal(visiblePreview(render(options)), null);
});

test('cross-file guidance appears for an attached TXT and CSV pair', () => {
  setup();
  const options = { onSendTextFiles: () => assert.fail('batch was sent') };
  const guidance = n => n.type === 'p' && String(n.props.children).includes('Compare the CSV against the TXT file.');
  selectBatch(render(options), [textFile, { ...textFile, name: 'other.txt' }]);
  assert.equal(find(render(options), guidance), null);
  selectBatch(render(options), [textFile, { name: 'readings.csv', type: 'text/csv', size: 28 }]);
  assert.ok(find(render(options), guidance));
});

test('a single JSON uses the record preview and sends through the identity-checked attachment route', async () => {
  setup();
  const file = { name: 'equipment.json', type: 'application/json', size: 24 };
  const options = { onSendTextFiles: (...args) => sent.push(args) };
  previewJson = async (upload, _session, _signal, range) => {
    assert.equal(upload, file);
    assert.equal(range, undefined);
    return { filename: file.name, size_bytes: file.size, headers: ['temperature (°C)'],
      column_types: ['Number'], rows: [['82']], row_count: 1, truncated: false };
  };
  await selectBatch(render(options), [file]);
  const preview = find(render(options), node => node.props?.['aria-label'] === 'JSON preview');
  assert.ok(find(preview, node => node.props?.scope === 'row' && node.props.children[0] === 0));
  question(render(options));
  assert.equal(send(render(options)).props.disabled, false);
  send(render(options)).props.onClick();
  assert.deepEqual(sent, [['What color?', { files: [file], source: 1 }, undefined]]);
  assert.equal(find(render(options), node => node.props?.['aria-label'] === 'JSON preview'), null);
});

test('TXT, CSV and JSON can share a request, with zero-based JSON selection and one-based form ranges', async () => {
  setup();
  const csv = { name: 'readings.csv', type: 'text/csv', size: 28 };
  const json = { name: 'equipment.json', type: 'application/json', size: 61 };
  const files = [textFile, csv, json];
  const options = { onSendTextFiles: (...args) => sent.push(args) };
  const previewRanges = [];
  previewJson = async (file, _session, _signal, range) => {
    assert.equal(file, json);
    previewRanges.push(range);
    return { filename: file.name, size_bytes: file.size, headers: ['vibration (mm/s)'],
      column_types: ['Number'], rows: range ? [['7.1']] : [['7.1'], ['4.6']], row_count: 2, truncated: false };
  };
  await selectBatch(render(options), files);
  let tree = render(options);
  assert.ok(find(tree, node => node.props?.['aria-label'] === 'Attach TXT, CSV or JSON files'));
  for (const [index, file] of files.entries()) {
    assert.ok(find(tree, node => node.type === 'span' && node.props.children.includes(index + 1) &&
      node.props.children.includes(file.name)));
  }
  const item = find(tree, node => node.type === 'li' && find(node, el => el.props?.['aria-label'] === 'Remove source 3'));
  await find(item, node => node.props?.type === 'radio').props.onChange();
  for (const label of ['From record index', 'To record index']) {
    const control = find(find(render(options), node => node.type === 'label' && node.props.children.includes(label)),
      node => node.props?.type === 'number');
    assert.equal(control.props.min, 0);
    assert.equal(control.props.max, 1);
    control.props.onChange({ target: { value: '0' } });
  }
  await find(render(options), node => node.type === 'button' && node.props.children.includes('Preview records')).props.onClick();
  tree = render(options);
  assert.deepEqual(previewRanges, [undefined, { start: 1, end: 1 }]);
  assert.ok(find(tree, node => node.props?.scope === 'row' && node.props.children.includes(0)));
  question(tree);
  send(render(options)).props.onClick();
  assert.deepEqual(sent, [['What color?', { files, source: 3 }, { start: 1, end: 1 }]]);
});

test('removing one source cancels its pending preview and preserves the remaining file', async () => {
  setup();
  const a = { ...textFile };
  const b = { ...textFile, name: 'other.txt' };
  const options = { onSendTextFiles: () => assert.fail('batch route was used'), onSendTextFile: (...args) => sent.push(args) };
  let complete, signal;
  preview = (file, _session, current) => {
    if (file === b) {
      signal = current;
      return new Promise(resolve => { complete = resolve; });
    }
    return Promise.resolve({ filename: a.name, size_bytes: a.size, text: 'first', line_count: 1, truncated: false });
  };
  selectBatch(render(options), [a, b]);
  const item = find(render(options), n => n.type === 'li' && find(n, el => el.props?.['aria-label'] === 'Remove source 2'));
  const pending = find(item, n => n.props?.type === 'radio').props.onChange();
  await find(render(options), n => n.props?.['aria-label'] === 'Remove source 2').props.onClick();
  assert.equal(signal.aborted, true);
  complete({ filename: b.name, size_bytes: b.size, text: 'late', line_count: 1, truncated: false });
  await pending;
  assert.equal(visiblePreview(render(options)).props.children[0], 'first');
  question(render(options));
  send(render(options)).props.onClick();
  assert.deepEqual(sent, [['What color?', a]]);
});

test('a batch over the size or count limit never previews or sends its files', () => {
  setup();
  const options = { onSendTextFiles: () => assert.fail('batch was sent') };
  const selected = { ...textFile, size: 1500 };
  for (const [files, message] of [
    [[selected, selected, selected], 'Combined file data must be at most 4 KiB'],
    [[textFile, textFile, textFile, textFile], 'Choose up to three files'],
  ]) {
    selectBatch(render(options), files);
    assert.ok(find(render(options), n => n.type === 'span' && String(n.props.children).includes(message)));
    assert.equal(find(render(options), n => n.props?.['aria-label'] === 'Remove source 1'), null);
  }
});

test('file capability gating is independent of text readiness and the picker accepts one file', () => {
  setup();
  const button = tree => find(tree, n => n.props?.['aria-label'] === 'Attach text or CSV file');
  const unavailable = render({ textFileSupported: false });
  assert.equal(button(unavailable).props.disabled, true);
  assert.match(String(find(unavailable, n => n.props?.id === 'chat-file-support').props.children), /authenticated local chat mode/);
  assert.equal(selectText(unavailable), undefined);
  const pendingModel = render({ sendBlocked: true });
  assert.equal(button(pendingModel).props.disabled, false);
  assert.equal(find(pendingModel, n => n.props?.id === 'text-file-input').props.multiple, undefined);
});

test('gated formats and oversize selections have accurate public errors', () => {
  setup();
  const error = tree => find(tree, n => n.props?.role === 'alert');
  selectText(render(), { name: 'report.pdf', type: 'application/pdf', size: 12 });
  assert.ok(find(error(render()), n => String(n.props?.children).includes('PDF, DOCX and XLSX chat attachments are unavailable')));
  selectText(render({ onSendTextFiles() {} }), {
    name: 'report.xlsx', type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', size: 12,
  });
  assert.ok(find(error(render()), n => String(n.props?.children).includes('XLSX chat attachments are unavailable')));
  selectText(render(), { ...textFile, size: 2049 });
  assert.ok(find(error(render()), n => String(n.props?.children).includes('exceeds 2 KiB')));
});

test('removal invalidates a captured submit handler and preserves the original long filename', async () => {
  setup();
  const file = { ...textFile, name: 'measurement-'.repeat(8) + '.txt' };
  const options = { onSendTextFile: (...args) => sent.push(args) };
  preview = async () => ({ filename: file.name, size_bytes: file.size, text: 'sample', line_count: 1, truncated: false });
  await selectText(render(options), file);
  let current = render(options);
  assert.ok(find(current, n => n.type === 'p' && n.props.children.includes(file.name)));
  question(current);
  current = render(options);
  const staleSubmit = send(current).props.onClick;
  find(current, n => n.type === 'button' && n.props.children.includes('Remove text file')).props.onClick();
  staleSubmit();
  assert.deepEqual(sent, []);
  assert.equal(visiblePreview(render(options)), null);
});

test('preview revocation clears the file and forwards only a public session message', async () => {
  setup();
  let expired = 0;
  preview = async () => { throw { status: 401, message: 'private stack and path' }; };
  await selectText(render({ onSessionExpired: () => { expired++; } }));
  assert.equal(expired, 1);
  assert.equal(visiblePreview(render()), null);
  const alert = find(render(), n => n.props?.role === 'alert');
  assert.ok(find(alert, n => String(n.props?.children).includes('session has expired')));
  assert.equal(find(alert, n => String(n.props?.children).includes('private stack')), null);
});

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
