import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import ts from 'typescript';
import React from 'react';
import * as conversations from '../src/researchConversation.ts';
import * as requests from '../src/researchRequests.ts';
import * as workspace from '../src/workspaceState.ts';

const require=createRequire(import.meta.url);
const result={claims:[],hits:[],terms:[],total_claims:0,note:'Local evidence'};
const summary={experts:[{id:'dram',name:'DRAM',enabled:1,paper_count:2}],counts:{},page_states:[]};
const response={id:'server-1',is_owner:true,question:'Question A',expert_ids:['dram'],expert_names:['DRAM'],status:'queued',phase:'queued',visibility:'private',local_evidence:result,result:null,created_at:'2026-10-20T00:00:00Z',updated_at:'2026-10-20T00:00:00Z',cache_hit:false};
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function find(root,predicate){if(!root||typeof root!=='object')return; if(predicate(root))return root;for(const child of React.Children.toArray(root.props?.children)){const found=find(child,predicate);if(found)return found;}}
function text(root){return typeof root==='string'?root:React.Children.toArray(root?.props?.children).map(text).join('');}
function harness({saved=[],server=[],report=null}={}){
 const states=[],refs=[],calls=[],pending=[],components=new Map();let si=0,ri=0;
 globalThis.location={hash:'#ask'};
 const hooks={useState(initial){const i=si++;if(!(i in states))states[i]=i===2?summary:typeof initial==='function'?initial():initial;return [states[i],value=>{states[i]=typeof value==='function'?value(states[i]):value;}];},useRef(initial){const i=ri++;return refs[i]??(refs[i]={current:initial});},useEffect(){}};
 const component=name=>{if(!components.has(name)){const value=()=>null;Object.defineProperty(value,'name',{value:name});components.set(name,value);}return components.get(name);};
 const reportState={report,open:!!report,selected:report?.document.sections[0],sectionId:report?'section':'',mode:'question',busy:false,historical:false,research:false,mobilePane:'chat',clear(){this.report=null;this.selected=null;},load(){return Promise.resolve();},select(){},setOpen(){},setMobilePane(){}};
 const api=(url,init={})=>{const d=deferred();calls.push({url,init});pending.push(d);return d.promise;};
 const data={get:(path)=>api('/api/pilot'+path),readView:()=> 'search',readHistory:()=>structuredClone(saved),readBookmarks:()=>[],viewHash:view=>'#'+view,viewNames:{},time:value=>value};
 const localRequire=name=>{
  if(name==='react')return hooks;
  if(!name.startsWith('.'))return require(name);
  if(name.endsWith('.css'))return {};
  if(name==='./identity')return {api,displayName:()=> 'Tester'};
  if(name==='./pilotData')return data;
  if(name==='./researchConversation')return conversations;
  if(name==='./researchRequests')return requests;
  if(name==='./workspaceState')return workspace;
  if(name==='./useReports')return {useReports:()=>reportState};
  if(name==='./reportData')return {reportFocus:r=>r?{report_id:r.id,report_section_id:'section',report_revision:r.revision}:{}};
  if(name==='./ProcessingActivity')return {useProcessing:()=>({}),ProcessingBadge:component('ProcessingBadge')};
  if(name==='./KnowledgeProgress')return {useKnowledgeProgress:()=>({items:[]}),useResearchRequests:()=>({items:server,total:server.length,error:''}),ResearchRequestHistory:component('ResearchRequestHistory'),KnowledgePreparation:component('KnowledgePreparation')};
  if(name==='./access')return {roleLabels:{admin:'Admin'}};
  if(name==='./researchAnswer')return {researchModeLabels:{deep:'Deep'},savedResearchMode:()=> 'deep'};
  return new Proxy({}, {get:(_,key)=>component(String(key))});
 };
 const module={exports:{}};
 const source=fs.readFileSync(path.resolve('src/PilotApp.tsx'),'utf8')+'\nexport {ResearchWorkspace};';
 new Function('require','module','exports',ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText)(localRequire,module,module.exports);
 const render=()=>{si=0;ri=0;return module.exports.ResearchWorkspace({session:{mode:'local_admin',user:{employee_id:'tester',role:'admin',permissions:{manage_experts:true}}}});};
 const getComponent=(root,name)=>find(root,node=>node.type===components.get(name));
 return {render,calls,pending,states,component:getComponent,reportState,button:(root,label)=>find(root,node=>node.type==='button'&&text(node)===label)};
}

test('navigation preserves a server registration and its late response cannot replace a new conversation',async()=>{
 const h=harness();let tree=h.render();
 h.component(tree,'ResearchComposer').props.onChange('Question A');tree=h.render();h.component(tree,'ResearchComposer').props.onSend();
 assert.equal(h.calls[0].url,'/api/pilot/research-requests');assert.equal(h.calls[0].init.signal,undefined);
 tree=h.render();assert.equal(h.component(tree,'ResearchDialogue').props.turns[0].status,'pending');
 h.button(tree,'새 대화 시작').props.onClick();tree=h.render();assert.deepEqual(h.component(tree,'ResearchDialogue').props.turns,[]);
 h.pending[0].resolve(response);await tick();tree=h.render();assert.deepEqual(h.component(tree,'ResearchDialogue').props.turns,[]);
 const history=h.states.find(value=>Array.isArray(value)&&value[0]?.turns);
 assert.equal(history[0].turns[0].request.id,'server-1');assert.equal(history[0].turns[0].status,'pending');
});
test('explicit stop while registration is pending cancels once its durable ID arrives',async()=>{
 const h=harness();let tree=h.render();h.component(tree,'ResearchComposer').props.onChange('Question A');tree=h.render();h.component(tree,'ResearchComposer').props.onSend();
 tree=h.render();h.component(tree,'ResearchComposer').props.onStop();assert.equal(h.calls.length,1);
 h.pending[0].resolve(response);await tick();assert.equal(h.calls[1].url,'/api/pilot/research-requests/server-1/cancel');
 h.pending[1].resolve({...response,status:'cancelled',updated_at:'2026-10-20T00:01:00Z'});await tick();
 assert.equal(h.component(h.render(),'ResearchDialogue').props.turns[0].status,'stopped');
});
test('a failed early cancellation preserves the registered request so it can still be opened',async()=>{
 const h=harness();let tree=h.render();h.component(tree,'ResearchComposer').props.onChange('Question A');tree=h.render();h.component(tree,'ResearchComposer').props.onSend();
 tree=h.render();h.component(tree,'ResearchComposer').props.onStop();h.pending[0].resolve(response);await tick();h.pending[1].reject(new Error('Cancel unavailable'));await tick();
 const turn=h.component(h.render(),'ResearchDialogue').props.turns[0];assert.equal(turn.request.id,'server-1');assert.equal(turn.status,'pending');
});
test('late privacy response updates its saved conversation, not the conversation currently open',async()=>{
 const turn=requests.requestTurn({...response,status:'completed',result:{...result,run_id:'run-1'}}),record=conversations.saveConversation('chat',[turn],'private');
 const h=harness({saved:[record]});let tree=h.render();h.button(tree,'Question A').props.onClick();
 h.pending[0].resolve({...response,status:'completed',result});await tick();tree=h.render();
 h.component(tree,'ResearchDialogue').props.onShare(h.component(tree,'ResearchDialogue').props.turns[0]);
 assert.match(h.calls[1].url,/visibility$/);h.button(tree,'새 대화 시작').props.onClick();
 h.pending[1].resolve({...response,status:'completed',visibility:'team',result,updated_at:'2026-10-20T00:02:00Z'});await tick();
 assert.deepEqual(h.component(h.render(),'ResearchDialogue').props.turns,[]);
 const history=h.states.find(value=>Array.isArray(value)&&value[0]?.turns);assert.equal(history[0].turns[0].request.visibility,'team');
});
test('followup submits owned history and focused report revision without losing the latest report UI',async()=>{
 const turn=requests.requestTurn({...response,status:'completed',result:{...result,run_id:'run-1'}}),record=conversations.saveConversation('chat',[turn],'private');record.reportId='report';
 const report={id:'report',revision:3,document:{sections:[{id:'section',title:'Retention'}]}};
 const h=harness({saved:[record],report});let tree=h.render();h.button(tree,'Question A').props.onClick();h.pending[0].resolve({...response,status:'completed',result:{...result,run_id:'run-1'}});await tick();
 tree=h.render();h.component(tree,'ResearchComposer').props.onChange('Which conditions?');tree=h.render();h.component(tree,'ResearchComposer').props.onSend();
 const payload=JSON.parse(h.calls[1].init.body);assert.deepEqual(payload.history_run_ids,['run-1']);assert.equal(payload.report_id,'report');assert.equal(payload.report_revision,3);assert.equal(payload.report_section_id,'section');assert.equal(payload.visibility,'private');
 h.pending[1].resolve({...response,id:'server-2',question:'Which conditions?'});await tick();
 assert.equal(h.component(h.render(),'ResearchDialogue').props.turns.length,2);
});
test('a failed history fetch does not invalidate the active registration completion',async()=>{
 const h=harness({server:[{...response,id:'stored',question:'Stored question'}]});let tree=h.render();
 h.component(tree,'ResearchComposer').props.onChange('Question A');tree=h.render();h.component(tree,'ResearchComposer').props.onSend();tree=h.render();
 h.button(tree,'Stored question').props.onClick();h.pending[1].reject(new Error('History unavailable'));await tick();
 h.pending[0].resolve({...response,status:'completed',result});await tick();tree=h.render();
 assert.equal(h.component(tree,'ResearchDialogue').props.turns[0].request.id,'server-1');
 assert.equal(h.component(tree,'ResearchComposer').props.busy,false);
});
test('history fetched after navigation never replaces a new conversation',async()=>{
 const h=harness({server:[{...response,id:'stored',question:'Stored question'}]});let tree=h.render();
 h.button(tree,'Stored question').props.onClick();h.button(tree,'새 대화 시작').props.onClick();
 h.pending[0].resolve({...response,id:'stored',question:'Stored question',status:'completed',result});await tick();
 assert.deepEqual(h.component(h.render(),'ResearchDialogue').props.turns,[]);
});
test('sidebar deduplicates server questions against local conversation groups',()=>{
 const record=conversations.saveConversation('chat',[requests.requestTurn(response)],'private');
 const h=harness({saved:[record],server:[response]}),tree=h.render();
 const recent=find(tree,node=>node.props?.className==='pilot-recent');
 const buttons=React.Children.toArray(recent.props.children).filter(node=>node.type==='button');
 assert.equal(buttons.length,1);assert.equal(text(buttons[0]),'Question A');
});
test('paper detail carries demand analysis separately from empty claims and links back to expert knowledge',async()=>{
 const h=harness();let tree=h.render();h.component(tree,'ResearchDialogue').props.onPaper('paper');
 const knowledge={coverage:{status:'ready',ready_chunks:2,total_chunks:2,review_state:'model_reviewed_human_pending'},common_wikis:[],experts:[{id:'dram',name:'DRAM',enabled:true}]};
 h.pending[0].resolve({id:'paper',title:'Paper',page_count:2,claims:[],pages:[],document_id:'doc',knowledge});await tick();tree=h.render();
 const panel=h.component(tree,'PaperKnowledge');assert.deepEqual(panel.props.knowledge,knowledge);
 assert.ok(text(tree).includes('별도의 구조화된 주장·수치 기록은 아직 없습니다.'));
 assert.ok(!text(tree).includes('아직 이 논문의 주장·수치 분석을 수행하지 않았습니다.'));
 panel.props.onExpert('dram');tree=h.render();assert.equal(location.hash,'#wiki');
 assert.equal(h.component(tree,'KnowledgePreparation').props.expert.id,'dram');
});
