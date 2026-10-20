import test from 'node:test';
import assert from 'node:assert/strict';
import {applyResearchRequest,detachConversationTurns,localOnlyQuestionCount,requestTurn,unavailableRequestTurn,recentResearchEntries} from '../src/researchRequests.ts';
import {conversationTurns,contextRunIds,saveConversation} from '../src/researchConversation.ts';

const evidence={claims:[],hits:[{id:'passage',text:'Source before answer'}],terms:[],total_claims:0,note:'Local evidence'};
const request={id:'r1',question:'Retention at 1 mA?',expert_ids:['dram'],expert_names:['DRAM'],is_owner:true,status:'queued',phase:'queued',visibility:'private',local_evidence:evidence,result:null,created_at:'2026-10-20T01:00:00Z',updated_at:'2026-10-20T01:00:00Z',cache_hit:false,history_run_ids:['prior-owned-run'],report_id:'report-owned',report_section_id:'section',report_revision:2,learning_eligible:false};

test('queued requests show local evidence and survive navigation and browser reload',()=>{
 const turn=requestTurn(request);
 assert.equal(turn.status,'pending');assert.deepEqual(turn.result,evidence);
 assert.equal(detachConversationTurns([turn])[0],turn);
 const reopened=conversationTurns(saveConversation('chat',[turn],'private'))[0];
 assert.equal(reopened.status,'pending');assert.equal(reopened.request.id,'r1');
 assert.equal(reopened.request.report_revision,2);
});
test('navigation stops legacy report streams without cancelling durable research',()=>{
 const durable=requestTurn(request),report={...durable,id:'report-edit',durable:false,request:undefined};
 const next=detachConversationTurns([durable,report]);
 assert.equal(next[0].status,'pending');assert.equal(next[1].status,'stopped');assert.equal(report.status,'pending');
});
test('completed answer replaces preview and retains history and report context',()=>{
 const final={...evidence,run_id:'new-run',note:'Reviewed result'};
 const turn=applyResearchRequest(requestTurn(request),{...request,status:'completed',result:final,updated_at:'2026-10-20T01:01:00Z'});
 assert.equal(turn.status,'completed');assert.deepEqual(turn.result,final);
 assert.deepEqual(contextRunIds([turn]),['prior-owned-run','new-run']);
 assert.equal(turn.request.report_section_id,'section');
});
test('shared results cannot add another owner private trace to a followup',()=>{
 const turn=requestTurn({...request,is_owner:false,status:'completed',result:{...evidence,run_id:'someone-else-private-run'}});
 assert.deepEqual(turn.historyRunIds,[]);assert.deepEqual(contextRunIds([turn]),[]);
});
test('late polling cannot undo explicit stop, completion or a later privacy change',()=>{
 const stopped=requestTurn({...request,status:'cancelled',updated_at:'2026-10-20T01:01:00Z'});
 assert.equal(applyResearchRequest(stopped,request),stopped);
 assert.equal(applyResearchRequest(stopped,{...request,status:'running',updated_at:'2026-10-20T01:02:00Z'}),stopped);
 const privateTurn=requestTurn({...request,status:'completed',updated_at:'2026-10-20T01:03:00Z'});
 assert.equal(applyResearchRequest(privateTurn,{...request,visibility:'team',updated_at:'2026-10-20T01:02:00Z'}),privateTurn);
});
test('registration interrupted before receiving server ID directs users to durable history',()=>{
 const turn={...requestTurn(request),request:undefined};
 const reopened=conversationTurns(saveConversation('chat',[turn],'private'))[0];
 assert.equal(reopened.status,'stopped');assert.match(reopened.message,/서버의 내 질문/);
});
test('profile counts browser only questions separately from server backed turns',()=>{
 const server=requestTurn(request),legacy={...server,request:undefined,durable:false};
 assert.equal(localOnlyQuestionCount([{deleted:false,turns:[server,legacy]},{deleted:false},{deleted:true,turns:[legacy]}]),2);
});
test('unchanged status polls reuse the evidence object and restore redacted shared entries only from the server',()=>{
 const turn=requestTurn(request);assert.equal(applyResearchRequest(turn,structuredClone(request)),turn);
 const hidden={...turn,result:undefined,status:'error',message:'Checking access'};
 const fresh=applyResearchRequest(hidden,request);assert.equal(fresh.status,'pending');assert.deepEqual(fresh.result,evidence);
});
test('withdrawn shared access clears cached evidence, stops pending UI and can recover after authorized reopen',()=>{
 const current=requestTurn({...request,is_owner:false});
 const hidden=unavailableRequestTurn(current);assert.equal(hidden.result,undefined);assert.equal(hidden.status,'error');
 assert.equal(hidden.request.id,request.id);assert.deepEqual(contextRunIds([hidden]),[]);
 assert.equal(applyResearchRequest(hidden,{...request,is_owner:false}).status,'pending');
});
test('recent list keeps a multi-turn conversation once and includes server-only history',()=>{
 const record=saveConversation('chat',[requestTurn(request),requestTurn({...request,id:'r2'})],'private');
 const entries=recentResearchEntries([record],[request,{...request,id:'r2'},{...request,id:'other'}]);
 assert.equal(entries.length,2);assert.equal(entries.filter(entry=>entry.kind==='conversation').length,1);
 assert.equal(entries.find(entry=>entry.kind==='request').request.id,'other');
 // Deleting a browser grouping must not hide the still-existing server request.
 assert.equal(recentResearchEntries([{...record,deleted:true}],[request])[0].kind,'request');
});
