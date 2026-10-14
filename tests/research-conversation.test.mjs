import test from 'node:test';
import assert from 'node:assert/strict';
import { contextRunIds, conversationTurns, readResearchStream, saveConversation } from '../src/researchConversation.ts';

const result={claims:[],hits:[],terms:[],total_claims:0,note:'Evidence',run_id:'run-1'};
const turn={id:'t1',question:'First?',createdAt:'2026-10-14T00:00:00Z',expertIds:['dram'],expertNames:['DRAM'],status:'completed',result};
test('conversation grouping keeps original title and independent answers',()=>{
 const turns=[turn,{...turn,id:'t2',question:'Followup?',result:{...result,run_id:'run-2'}}];
 const record=saveConversation('chat',turns,'private');
 assert.equal(record.question,'First?');
 assert.deepEqual(conversationTurns(record),turns);
 assert.equal(record.result.run_id,'run-2');
 record.turns[0].question='Edited';
 assert.equal(turns[0].question,'First?');
});
test('legacy answers reopen and interrupted turns do not remain pending',()=>{
 const record=saveConversation('chat',[turn],'team');delete record.turns;
 assert.equal(conversationTurns(record)[0].result.run_id,'run-1');
 const pending=saveConversation('chat',[{...turn,status:'pending',result:undefined}],'private');
 assert.equal(conversationTurns(pending)[0].status,'stopped');
 pending.turns=[{id:'broken'}];
 assert.equal(conversationTurns(pending).length,1);
});
test('context contains only last six unique completed server runs',()=>{
 const turns=Array.from({length:9},(_,i)=>({...turn,id:String(i),result:{...result,run_id:'run-'+i}}));
 turns.push({...turn,status:'stopped',result:{...result,run_id:'bad'}},{...turn,status:'error'});
 assert.deepEqual(contextRunIds(turns),['run-3','run-4','run-5','run-6','run-7','run-8']);
});
function response(text,width=3){
 const bytes=new TextEncoder().encode(text);
 return new Response(new ReadableStream({start(controller){for(let i=0;i<bytes.length;i+=width)controller.enqueue(bytes.slice(i,i+width));controller.close();}}));
}
test('NDJSON supports split UTF8, heartbeats and progress preceding final result',async()=>{
 const events=[];
 const lines=[{type:'progress',stage:'search',message:'근거 검색 중'},{type:'experts',ids:['dram'],names:['DRAM']},{type:'result',result}];
 const answer=await readResearchStream(response('\n'+lines.map(JSON.stringify).join('\n')+'\n'),new AbortController().signal,e=>events.push(e));
 assert.deepEqual(answer,result);
 assert.deepEqual(events,lines.slice(0,2));
});
test('truncated stream and server errors are not successful answers',async()=>{
 await assert.rejects(()=>readResearchStream(response('{"type":"progress","stage":"search","message":"Searching"}'),new AbortController().signal,()=>{}),/연결이 종료/);
 await assert.rejects(()=>readResearchStream(response('{"type":"error","message":"Missing expert"}\n'),new AbortController().signal,()=>{}),/Missing expert/);
});
test('abort cancels reader without publishing a late result',async()=>{
 const controller=new AbortController();let cancelled=false;
 const res=new Response(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('{"type":"progress","stage":"answer","message":"Writing"}\n'));},cancel(){cancelled=true;}}));
 const promise=readResearchStream(res,controller.signal,()=>controller.abort());
 await assert.rejects(promise,{name:'AbortError'});
 assert.equal(cancelled,true);
});
