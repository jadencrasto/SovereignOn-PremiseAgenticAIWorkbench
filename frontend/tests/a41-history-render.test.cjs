const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),path=require('path'),Module=require('module'),ts=require('typescript'),React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const filename=path.resolve(__dirname,'../src/components/chat/MessageItem.tsx');
const output=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText;
const m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(path.dirname(filename));const orig=m.require.bind(m);
const mocks={'./MarkdownContent':{MarkdownContent:({content})=>React.createElement('div',null,content)},'./SourceCard':{SourceCard:()=>React.createElement('div',null,'source-secret')},'../agent/PlanTimeline':{PlanTimeline:()=>React.createElement('div',null,'plan-secret')},'../agent/ApprovalCard':{ApprovalCard:()=>React.createElement('div',null,'approval-secret')}};
m.require=n=>Object.hasOwn(mocks,n)?mocks[n]:orig(n);m._compile(output,filename);
test('withheld response never renders sensitive content, sources or copy control',()=>{const message={id:'x',role:'assistant',content:'synthetic-secret',timestamp:'',withheld:true,sources:[{document_id:'s'}],toolEvents:[{type:'tool_result',tool:'calculator',success:true,summary:'tool-secret'}],plan:{taskId:'t',objective:'plan-secret',steps:[{id:'s'}]}};const html=renderToStaticMarkup(React.createElement(m.exports.MessageItem,{message}));assert.match(html,/response is withheld/i);assert.doesNotMatch(html,/synthetic-secret|source-secret|tool-secret|plan-secret/);assert.doesNotMatch(html,/>Copy</)});

test('visual observations render as inert text and removed images have no preview', () => {
 const render = message => renderToStaticMarkup(React.createElement(m.exports.MessageItem, {message}));
 const answer = render({id:'v', role:'assistant', timestamp:'', localVisual:true,
   content:'Model-generated image observations\n\n<img src="https://example.invalid/image">'});
 assert.match(answer, /Model-generated image observations/);
 assert.match(answer, /&lt;img/);
 assert.doesNotMatch(answer, /<img\b/);
 const history = render({id:'u', role:'user', timestamp:'', content:'What is visible?',
   attachments:[{id:'i',localAvailable:false, filename:'example.png', mimeType:'image/png', sizeBytes:24}]});
 assert.match(history, /Image removed or unavailable for reuse/);
 assert.doesNotMatch(history, /<img\b/);
});

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

test('persisted local clarification tells the user how to name a result or supply units', () => {
 const message = { id: 'turn', role: 'user', content: 'Subtract 5 from the conversion result.', timestamp: '',
   localTurn: { state: 'failed', error_code: 'LOCAL_TOOL_REFERENCE_UNIT' } };
 let html = renderToStaticMarkup(React.createElement(m.exports.MessageItem, { message }));
 assert.match(html, /Give both measurements a unit/);
 message.localTurn.error_code = 'LOCAL_TOOL_CONTEXT';
 html = renderToStaticMarkup(React.createElement(m.exports.MessageItem, { message }));
 assert.match(html, /result from the first calculation/);
 assert.doesNotMatch(html, /tool_start|numeric_tool|invocation_id/);
});

test('reconciled measurement output shows source attribution and unit steps', () => {
 const message = { id: 'answer', role: 'assistant', timestamp: '', numericSourceTurn: 2,
   content: '**Result: 5250 m**\n\n1. Converted to 0.25 km.\n2. Calculated 5.25 km.\n3. Converted to 5250 m.' };
 const render = () => renderToStaticMarkup(React.createElement(m.exports.MessageItem, { message }));
 assert.match(render(), /Reused verified result from turn 2/);
 assert.match(render(), /Result: 5250 m/);
 assert.match(render(), /Converted to 0\.25 km/);
 message.withheld = true;
 assert.doesNotMatch(render(), /Reused verified result|5250 m/);
});

test('temperature difference shows both verified source turns and absolute-zero errors are clear', () => {
 const message = { id: 'delta', role: 'assistant', timestamp: '', numericSourceTurn: 1, numericSecondarySourceTurn: 3,
   content: '**Result: 10 Δ°C (temperature difference)**' };
 let html = renderToStaticMarkup(React.createElement(m.exports.MessageItem, { message }));
 assert.match(html, /10 Δ°C \(temperature difference\)/);
 assert.match(html, /Reused verified results from turns 1 and 3/);
 const failed = { id: 'bad', role: 'user', timestamp: '', content: '0 K - 1 ΔK',
   localTurn: { state: 'failed', error_code: 'LOCAL_TOOL_ABSOLUTE_ZERO' } };
 html = renderToStaticMarkup(React.createElement(m.exports.MessageItem, { message: failed }));
 assert.match(html, /cannot be below absolute zero \(0 K\)/);
});

test('local citations render literal evidence and stale evidence is hidden', () => {
 const message = { id: 'answer', role: 'assistant', timestamp: '',
   content: '“<img src=hidden> Pressure limit: 75 C.” [1]',
   localKnowledge: { state: 'grounded', citations: [{ number: 1, label: 'limits.md', revision: 2,
     passage_id: 'owned_chunk_0', line: 3, excerpt: '<img src=hidden> Pressure limit: 75 C.' }] } };
 const render = () => renderToStaticMarkup(React.createElement(m.exports.MessageItem, { message }));
 let html = render();
 assert.match(html, /Verified local citations/);
 assert.match(html, /\[1\] limits\.md.*line 3.*revision 2/);
 assert.match(html, /&lt;img src=hidden&gt;/);
 assert.doesNotMatch(html, /<img src=hidden>/);
 message.withheld = true;
 message.localKnowledge.state = 'unavailable';
 html = render();
 assert.match(html, /Local evidence is unavailable/);
 assert.doesNotMatch(html, /Pressure limit|limits\.md|Verified local citations/);
});

test('multipart coverage links to real citations and shows uncovered parts', () => {
 const message = { id: 'answer', role: 'assistant', timestamp: '', content: 'Partial answer',
   localKnowledge: { state: 'partial', coverage: [
     { question: 'What is operating pressure?', state: 'supported', citations: [1] },
     { question: 'What is the alarm threshold?', state: 'absent', citations: [] },
   ], citations: [{ number: 1, label: 'limits.md', revision: 3, passage_id: 'owned_chunk_0',
     line: 8, heading: '# Operating limits', heading_line: 7, excerpt: 'Pressure: 3 bar.' }] } };
 const html = renderToStaticMarkup(React.createElement(m.exports.MessageItem, { message }));
 assert.match(html, /Cited evidence.*operating pressure/);
 assert.match(html, /No evidence.*alarm threshold/);
 assert.match(html, /href="#evidence-answer-1"/);
 assert.match(html, /id="evidence-answer-1"/);
 assert.match(html, /Section # Operating limits.*line 7/);
 const turn = { id: 'question', role: 'user', timestamp: '', content: 'Original question', localTurn: {
   state: 'completed', knowledge_scope_stale: true, knowledge_scope: {
     mode: 'selected', sources: [{ label: 'limits.md', revision: 3 }],
   },
 } };
 const scope = renderToStaticMarkup(React.createElement(m.exports.MessageItem, { message: turn }));
 assert.match(scope, /Selected local sources.*limits\.md.*rev\. 3.*pinned revision is unavailable/);
});

test('technical citations show section ancestry and bounded match reasons as literal text', () => {
 const message = { id: 'technical', role: 'assistant', timestamp: '', content: 'Cited temperature threshold',
   localKnowledge: { state: 'grounded', citations: [{
     number: 1, label: 'manual.md', revision: 2, passage_id: 'chunk_0', line: 9, line_end: 9,
     sections: [{ id: 'root', title: 'Plant <manual>', line: 1, level: 1 },
       { id: 'limits', title: 'Operating limits', line: 5, level: 2 }],
     equipment: ['HX-204'], measurements: ['80 °C'], match_reasons: ['equipment', 'equivalent_measurement'],
     excerpt: 'HX-204 threshold: 80 °C.',
   }] } };
 const html = renderToStaticMarkup(React.createElement(m.exports.MessageItem, { message }));
 assert.match(html, /manual\.md.*line 9.*revision 2/);
 assert.match(html, /Plant &lt;manual&gt; \(line 1\) \/ Operating limits \(line 5\)/);
 assert.match(html, /Equipment.*HX-204/);
 assert.match(html, /Source measurements.*80 °C/);
 assert.match(html, /Equivalent measurement in a supported unit/);
});

test('dense evidence shows only the selected field as an attributed value', () => {
 const message = { id: 'dense', role: 'assistant', timestamp: '', content: 'Verified source value',
   localKnowledge: {
     state: 'grounded',
     coverage: [{ question: 'Normal pressure?', state: 'supported', citations: [1],
       fields: [{ citation: 1, attribute: 'pressure', value: '3', unit: 'bar',
         equipment: ['P-101'], conditions: ['normal'], line: 4, column: 3, header: 'Pressure <bar>',
         header_line: 2, start: 60, end: 61 }],
     }],
     citations: [{ number: 1, label: 'table.md', revision: 2, passage_id: 'chunk_0', line: 4,
       measurements: ['3 bar', '9 bar'], excerpt: '| P-101 | Normal | 3 bar | 9 bar |' }],
   } };
 const html = renderToStaticMarkup(React.createElement(m.exports.MessageItem, { message }));
 assert.match(html, /Attributed source values.*<strong>3 bar<\/strong>.*Pressure &lt;bar&gt;.*P-101.*normal/);
 assert.match(html, /href="#evidence-dense-1">line 4, column 3<\/a>/);
 assert.match(html, /table\.md.*line 4.*revision 2/);
 assert.doesNotMatch(html, /Source measurements|<strong>9 bar<\/strong>/);
});

test('local document evidence exposes native positions and revision without projected line labels', () => {
 const citations = [
   { label: 'limits.pdf', position: { format: 'pdf', page: 2, block: 1 } },
   { label: 'maintenance.docx', position: { format: 'docx', table: 1, row: 4 } },
   { label: 'register.xlsx', position: { format: 'xlsx', sheet: 'Register <A>', range: 'A4:D4' } },
 ];
 const message = { id: 'documents', role: 'assistant', timestamp: '', content: 'Document measurements',
   localKnowledge: { state: 'grounded', citations: citations.map((citation, index) => ({
     label: citation.label, passage_id: String(index), number: index + 1, revision: 2, line: 99,
     document_locator: citation.position, excerpt: 'Verified value',
   })), coverage: [{ question: 'Normal torque?', state: 'supported', citations: [3],
     fields: [{ citation: 3, attribute: 'torque', value: '26 N·m', unit: 'N·m',
       equipment: ['P-503'], conditions: ['normal'], start: 10, end: 15, line: 99, column: 4,
       document_locator: { ...citations[2].position, cell: 'D4' } }],
   }] },
 };
 const html = renderToStaticMarkup(React.createElement(m.exports.MessageItem, { message }));
 assert.match(html, /limits\.pdf.*page 2.*block 1.*revision 2/);
 assert.match(html, /maintenance\.docx.*table 1, row 4.*revision 2/);
 assert.match(html, /Register &lt;A&gt;.*cell D4/);
 assert.match(html, /register\.xlsx.*range A4:D4.*revision 2/);
 assert.doesNotMatch(html, /line 99/);
});
