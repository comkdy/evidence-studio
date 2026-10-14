import test from 'node:test';
import assert from 'node:assert/strict';
import { answerCopyText, answerLimitations, savedResearchMode } from '../src/researchAnswer.ts';
import { readHistory } from '../src/pilotData.ts';

const legacyResult={claims:[],hits:[],terms:[],note:'Stored evidence note',total_claims:0,answers:[{expert_id:'dram',expert_name:'DRAM',markdown:'Original answer',mode:'llm',wikis:[]}]};
const legacyRecord={id:'old',question:'Retention?',expertIds:['dram'],expertNames:['DRAM'],createdAt:'2026-10-13T00:00:00Z',visibility:'private',deleted:false,result:legacyResult};
const currentResult={...legacyResult,synthesis:{markdown:'Combined conclusion with citation',mode:'llm',notice:'Support review remains limited',wikis:[]},research:{mode:'deep',queries:['retention'],limitations:['Different temperatures','Unreported test duration','Different temperatures'],paper_count:3,rounds:2,validation_status:'checked'},run_id:'research-1'};

test('copy preserves the synthesis, caveats, expert perspective and exact evidence',()=>{
 const text=answerCopyText('Retention?',['DRAM'],currentResult,['1.20e-3 A at 85 C']);
 assert.ok(text.indexOf('Combined conclusion')<text.indexOf('Original answer'));
 for(const part of ['Different temperatures','Unreported test duration','Support review remains limited','3편','심층','1.20e-3 A at 85 C'])assert.ok(text.includes(part),part);
 assert.equal(text.split('Different temperatures').length-1,1);
 assert.deepEqual(answerLimitations(currentResult),['Different temperatures','Unreported test duration']);
});

test('fallback copy identifies missing generation instead of claiming a completed synthesis',()=>{
 const result={...currentResult,synthesis:{markdown:'Sources only',mode:'evidence',notice:'Model unavailable',wikis:[]},answers:[]};
 const text=answerCopyText('Retention?',[],result,[]);
 assert.ok(text.includes('종합 답변 미생성'));
 assert.ok(text.includes('Model unavailable'));
 assert.ok(!text.includes('종합 판단 · 근거 기반 답변'));
});

test('legacy saved answers retain their contents and default to basic mode',()=>{
 assert.equal(savedResearchMode(legacyRecord),'basic');
 const text=answerCopyText(legacyRecord.question,legacyRecord.expertNames,legacyResult,[]);
 assert.ok(text.includes('Original answer'));
 assert.ok(!text.includes('종합 판단'));
 assert.equal(savedResearchMode({...legacyRecord,mode:'deep'}),'deep');
 assert.equal(savedResearchMode({...legacyRecord,mode:'basic',result:currentResult}),'deep');
});

test('private and team history round trips retain synthesis and limitations alongside legacy entries',()=>{
 const records=[legacyRecord,{...legacyRecord,id:'new',visibility:'team',mode:'deep',result:currentResult}];
 const original=globalThis.localStorage;
 globalThis.localStorage={getItem:()=>JSON.stringify(records)};
 try{
  const restored=readHistory('test-user');
  assert.equal(restored.length,2);
  assert.deepEqual(restored[0],legacyRecord);
  assert.deepEqual(restored[1].result,currentResult);
  assert.equal(savedResearchMode(restored[1]),'deep');
 }finally{if(original===undefined)delete globalThis.localStorage;else globalThis.localStorage=original;}
});
