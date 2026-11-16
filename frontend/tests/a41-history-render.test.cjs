const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),path=require('path'),Module=require('module'),ts=require('typescript'),React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const filename=path.resolve(__dirname,'../src/components/chat/MessageItem.tsx');
const output=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText;
const m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));const orig=m.require.bind(m);
const mocks={'./MarkdownContent':{MarkdownContent:({content})=>React.createElement('div',null,content)},'./SourceCard':{SourceCard:()=>React.createElement('div',null,'source-secret')},'../agent/PlanTimeline':{PlanTimeline:()=>React.createElement('div',null,'plan-secret')},'../agent/ApprovalCard':{ApprovalCard:()=>React.createElement('div',null,'approval-secret')}};
m.require=n=>Object.hasOwn(mocks,n)?mocks[n]:orig(n);m._compile(output,filename);
test('withheld response never renders sensitive content, sources or copy control',()=>{const message={id:'x',role:'assistant',content:'synthetic-secret',timestamp:'',withheld:true,sources:[{document_id:'s'}],toolEvents:[{type:'tool_result',tool:'calculator',success:true,summary:'tool-secret'}],plan:{taskId:'t',objective:'plan-secret',steps:[{id:'s'}]}};const html=renderToStaticMarkup(React.createElement(m.exports.MessageItem,{message}));assert.match(html,/response is withheld/i);assert.doesNotMatch(html,/synthetic-secret|source-secret|tool-secret|plan-secret/);assert.doesNotMatch(html,/>Copy</)});

test('repeated tool invocation stays running until its own result arrives',()=>{
 const message={id:'x',role:'assistant',content:'',timestamp:'',isStreaming:true,toolEvents:[{type:'tool_start',tool:'calculator'},{type:'tool_result',tool:'calculator',success:true},{type:'tool_start',tool:'calculator'}]};
 const html=renderToStaticMarkup(React.createElement(m.exports.MessageItem,{message}));
 assert.match(html,/>Running</);assert.doesNotMatch(html,/>Results received</);
});
test('interrupted in-flight tool is shown as uncertain and unknown results are not success',()=>{
 const message={id:'x',role:'assistant',content:'',timestamp:'',isStreaming:false,toolEvents:[{type:'tool_start',tool:'calculator'}]};
 assert.match(renderToStaticMarkup(React.createElement(m.exports.MessageItem,{message})),/Outcome uncertain/);
 message.toolEvents=[{type:'tool_result',tool:'calculator'}];
 const html=renderToStaticMarkup(React.createElement(m.exports.MessageItem,{message}));
 assert.match(html,/Outcome unknown/);assert.doesNotMatch(html,/>Results received</);
});

test('failed stream renders partial text and an error without offering retry', () => {
 const message = { id: 'x', role: 'assistant', timestamp: '', content: 'Partial answer',
   error: true, errorMessage: 'Model provider unavailable', toolEvents: [{ type: 'tool_start', tool: 'calculator' }] };
 const html = renderToStaticMarkup(React.createElement(m.exports.MessageItem, { message }));
 assert.match(html, /Response failed/);
 assert.match(html, /Model provider unavailable/);
 assert.match(html, /Partial answer/);
 assert.match(html, /Outcome uncertain/);
 assert.doesNotMatch(html, />Retry</);
});
