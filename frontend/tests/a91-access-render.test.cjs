const {test}=require('node:test'), assert=require('node:assert/strict');
const fs=require('node:fs'), path=require('node:path'), Module=require('node:module');
const ts=require('typescript'), React=require('react'), {renderToStaticMarkup}=require('react-dom/server');
const filename=path.resolve(__dirname,'../src/components/auth/RolePortalView.tsx');
const js=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText;
const m=new Module(filename,module); m.filename=filename; m.paths=Module._nodeModulePaths(path.dirname(filename));
const original=m.require.bind(m); m.require=name=> name==='../../context/AuthContext'?{useAuth:()=>({user:{id:'a'},isLoading:false})}:name==='../../context/WorkbenchContext'?{useWorkbench:()=>({capabilities:null,isCheckingHealth:false})}:name==='../../api/client'?{apiRequest:()=>{throw Error('unexpected render fetch')}}:name==='../../api/capabilities'?{capabilityFor:()=>({state:'unknown'}),describeCapability:()=> 'Status unknown'}:original(name);m._compile(js,filename);
test('access view renders no simulated account actions and unknown capability status',()=>{
 const html=renderToStaticMarkup(React.createElement(m.exports.RolePortalView));
 assert.match(html,/Feature status/); assert.match(html,/unknown/); assert.match(html,/Account changes are not available/);
 assert.doesNotMatch(html,/admin_lead|plant_manager_04|Created new|Revoked access/);
});
