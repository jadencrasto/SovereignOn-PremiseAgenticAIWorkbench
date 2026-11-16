// Tests real sidebar handlers with mocked hooks and API; not a mounted DOM.
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),path=require('path'),Module=require('module'),ts=require('typescript');
let slots=[],cursor=0,effects=[],effectCleanups=[],records={},ids=[],deleted=[],renamed=[],selected=[];
const React={createElement:(type,props,...children)=>({type,props:{...props,children}}),useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v}]},useRef(initial){const i=cursor++;return slots[i]??={current:initial}},useMemo(f){cursor++;return f()},useEffect(f){cursor++;effects.push(f)}};
const mocksApi={fetchSessions:async()=>({sessions:ids,count:ids.length}),fetchSession:async id=>{if(!records[id])throw Error('unavailable');return records[id]},legacyExportAllowed:async()=>({allowed:false}),clearSession:async id=>{deleted.push(id);delete records[id];ids=ids.filter(x=>x!==id)},renameSession:async(id,title)=>{renamed.push([id,title]);records[id].title=title;return {title}}};
const history={loadOwnedHistory:async(list,read,current)=>{const {sessions}=await list();if(!current())return null;const items=await Promise.all([...new Set(sessions)].map(read));return current()?items:null},deleteOwnedHistory:async(id,remove,current)=>{await remove(id);return current()}};
class ApiError extends Error { constructor(message,status){super(message);this.status=status} }
const mocks={react:React,'../../api/client':{ApiError},'../../api/chat':mocksApi,'./historyRequests':history,'lucide-react':new Proxy({},{get:()=>()=>null})};
const file=path.resolve(__dirname,'../src/components/chat/ChatHistorySidebar.tsx');const out=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText;const mod=new Module(file,module);mod.filename=file;mod.paths=Module._nodeModulePaths(path.dirname(file));const original=mod.require.bind(mod);mod.require=n=>Object.hasOwn(mocks,n)?mocks[n]:original(n);mod._compile(out,file);
const props={isOpen:true,onClose(){},currentSessionId:'a',onSelectSession:s=>selected.push(s),onNewChat(){},onRenamed:(id,title)=>renamed.push(['parent',id,title])};
function render(){cursor=0;effects=[];return mod.exports.ChatHistorySidebar(props)}
function find(node,pred){if(!node||typeof node!=='object')return;if(Array.isArray(node)){for(const n of node){const x=find(n,pred);if(x)return x}return}if(pred(node))return node;for(const c of node.props?.children||[]){const x=find(c,pred);if(x)return x}}
const byTitle=(node,title)=>find(node,n=>n.props?.title===title);
const tick=()=>new Promise(r=>setImmediate(r));
async function setup(){slots=[];ids=['a','a'];deleted=[];renamed=[];selected=[];records={a:{session_id:'a',title:'Original',created_at:'2026-01-01T00:00:00Z',updated_at:'2026-01-02T00:00:00Z',messages:[{id:'u',role:'user',content:'hello'},{id:'x',role:'assistant',content:'[Response withheld: source access cannot be verified.]',withheld:true}]}};global.window={confirm:()=>true};render();effectCleanups=effects.map(effect=>effect());await tick();await tick();return render()}
const event={stopPropagation(){}};
test('image history carries attachment metadata without a preview URL', async () => {
  records = { a: { session_id: 'a', messages: [{
    role: 'user', content: 'Describe this image', attachments: [{
      attachment_id: 'image-1', filename: 'sample.png', mime_type: 'image/png', size_bytes: 32,
    }],
  }] } };
  const session = await mod.exports.loadSession('a');
  assert.deepEqual(session.messages[0].attachments, [{
    id: 'image-1', filename: 'sample.png', mimeType: 'image/png', sizeBytes: 32,
  }]);
});
test('sidebar loads server records without duplication and preserves withheld state',async()=>{const tree=await setup();const row=find(tree,n=>n.type==='div'&&n.props?.className?.includes('group p-3'));assert.ok(row);await row.props.onClick();assert.equal(selected.length,1);assert.equal(selected[0].messages[1].withheld,true);assert.equal(selected[0].createdAt,Date.parse('2026-01-01T00:00:00Z'))});
test('history maps persisted incomplete responses to their visible states',async()=>{const tree=await setup();records.a.messages[1].response_state='interrupted';const row=find(tree,n=>n.type==='div'&&n.props?.className?.includes('group p-3'));await row.props.onClick();assert.equal(selected[0].messages[1].workflowStatus,'interrupted')});
test('sidebar rename uses confirmed server title and updates parent',async()=>{let tree=await setup();byTitle(tree,'Rename').props.onClick(event);tree=render();const field=find(tree,n=>n.type==='input'&&n.props?.value==='Original');field.props.onChange({target:{value:'Renamed'}});tree=render();await byTitle(tree,'Save').props.onClick(event);assert.deepEqual(renamed,[['a','Renamed'],['parent','a','Renamed']]);assert.ok(find(render(),n=>n.type==='h3'&&n.props.children.includes('Renamed')))});
test('sidebar deletion waits for confirmation and server response',async()=>{let tree=await setup();global.window.confirm=()=>false;byTitle(tree,'Delete').props.onClick(event);assert.deepEqual(deleted,[]);global.window.confirm=()=>true;byTitle(tree,'Delete').props.onClick(event);await tick();assert.deepEqual(deleted,['a']);assert.equal(find(render(),n=>n.type==='h3'&&n.props.children.includes('Original')),undefined)});
test('delayed deletion does not clear a newly selected conversation',async()=>{props.currentSessionId='a';let cleared=0;const oldCallback=props.onNewChat,oldRemove=mocksApi.clearSession;props.onNewChat=()=>cleared++;let resolve;try{const tree=await setup();mocksApi.clearSession=()=>new Promise(r=>{resolve=r});byTitle(tree,'Delete').props.onClick(event);props.currentSessionId='b';render();resolve();await tick();assert.equal(cleared,0)}finally{props.currentSessionId='a';props.onNewChat=oldCallback;mocksApi.clearSession=oldRemove}});
test('sidebar export revalidates access before reading cached content',async()=>{let tree=await setup();delete records.a;await byTitle(tree,'Export Markdown').props.onClick(event);assert.ok(find(render(),n=>n.props?.role==='alert'))});

test('sidebar shows loading and an error when the server request fails',async()=>{let rejectList;const previous=mocksApi.fetchSessions;mocksApi.fetchSessions=()=>new Promise((_resolve,reject)=>{rejectList=reject});slots=[];ids=[];records={};global.window={confirm:()=>true};render();for(const effect of effects)effect();let tree=render();assert.ok(find(tree,n=>n.props?.role==='status'&&n.props.children.includes('Loading conversations...')));rejectList(Error('offline'));await tick();await tick();tree=render();assert.ok(find(tree,n=>n.props?.role==='alert'));mocksApi.fetchSessions=previous});

test('an explicit history selection survives a background list refresh but not drawer cleanup', async () => {
  for (const closing of [false, true]) {
    const tree = await setup();
    const row = find(tree, node => node.props?.role === 'group');
    assert.ok(row);
    let finishRead;
    const fetchSession = mocksApi.fetchSession;
    mocksApi.fetchSession = () => new Promise(resolve => { finishRead = resolve; });
    const pending = row.props.onClick();
    mocksApi.fetchSession = fetchSession;
    if (closing) {
      for (const cleanup of effectCleanups) cleanup?.();
    } else {
      effectCleanups.at(-1)?.();
      props.refreshKey = 1;
      render();
      effects.at(-1)();
      await tick();
      props.refreshKey = undefined;
    }
    finishRead(records.a);
    await pending;
    assert.deepEqual(selected.map(session => session.id), closing ? [] : ['a']);
  }
});
