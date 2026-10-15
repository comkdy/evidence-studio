import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {reportFocus,readReportStream} from '../src/reportData.ts';
import {conversationTurns,saveConversation,contextRunIds} from '../src/researchConversation.ts';

const section={id:'s1',title:'Retention',layout:'prose',statements:[],sources:{}};
const report={id:'report',revision:2,current_revision:2,updated_at:'2026-10-15T00:00:00Z',document:{title:'Memory report',purpose:'Team review',sections:[section]},usage:{},instruction:'edit',model:'internal-model'};
test('focus sends identifiers and revision only; old or missing sections cannot be targeted',()=>{
 assert.deepEqual(reportFocus(report,'s1'),{report_id:'report',report_section_id:'s1',report_revision:2});
 assert.deepEqual(reportFocus({...report,current_revision:3},'s1'),{});
 assert.deepEqual(reportFocus(report,'missing'),{});
 assert.deepEqual(reportFocus(null,'s1'),{});
});
test('report stream publishes only complete document and reports validation failure',async()=>{
 const progress={type:'progress',stage:'report_verify',message:'인용 검증 중'};const events=[];
 const response=new Response('\n'+JSON.stringify(progress)+'\n'+JSON.stringify({type:'report_result',result:report})+'\n');
 const result=await readReportStream(response,new AbortController().signal,e=>events.push(e));
 assert.deepEqual(result,report);assert.deepEqual(events,[progress]);
 await assert.rejects(()=>readReportStream(new Response('{"type":"error","message":"수치 불일치"}\n'),new AbortController().signal,()=>{}),/수치 불일치/);
 await assert.rejects(()=>readReportStream(new Response('{"type":"report_result","result":{}}\n'),new AbortController().signal,()=>{}),/형식/);
});
test('report edit turns restore alongside Q&A without turning drafts into research evidence',()=>{
 const turn={id:'t1',question:'이 구간을 요약해 줘',createdAt:'now',expertIds:[],expertNames:[],status:'completed',focusTitle:'Retention',reportChange:{reportId:'report',revision:2,sectionTitle:'Retention',model:'test',elapsedMs:100}};
 const saved=saveConversation('chat',[turn],'private');
 assert.deepEqual(conversationTurns(saved),[turn]);
 assert.deepEqual(contextRunIds([turn]),[]);
});

const require=createRequire(import.meta.url),cache=new Map();
function load(file){
 if(cache.has(file))return cache.get(file).exports;
 const module={exports:{}};cache.set(file,module);
 const compiled=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 const localRequire=name=>{if(name.endsWith('.css'))return {};if(!name.startsWith('.'))return require(name);const base=path.resolve(path.dirname(file),name);return load(fs.existsSync(base)?base:fs.existsSync(base+'.ts')?base+'.ts':base+'.tsx');};
 new Function('require','module','exports',compiled)(localRequire,module,module.exports);return module.exports;
}
const {ReportPane}=load(path.resolve('src/ReportPane.tsx'));
const {ResearchComposer}=load(path.resolve('src/ResearchComposer.tsx'));
function pane(overrides={}){return renderToStaticMarkup(React.createElement(ReportPane,{locked:false,onPaper:()=>{},state:{report,list:[{id:'report',title:'Memory report'}],versions:[{revision:2,instruction:'shorten',created_at:report.updated_at}],sectionId:'s1',historical:false,busy:false,error:'',...overrides}}));}
test('report pane exposes selectable section, scoped settings, history and exact revision download',()=>{
 const html=pane();
 for(const label of ['Retention 구간 선택','이전 버전 복원','제목 · 표시 형식','HTML 다운로드','본인만 열람'])assert.ok(html.includes(label));
 assert.ok(html.includes('/export?revision=2'));
 assert.ok(html.includes('aria-pressed="true"'));
 const historical=pane({historical:true,report:{...report,current_revision:3}});
 assert.ok(historical.includes('이 버전으로 복원'));
 assert.ok(/disabled=""[^>]*aria-pressed="true"/.test(historical));
});
test('sources and report contents escape markup; original document link retains page',()=>{
 const ev={kind:'source_text',paper_title:'<script>unsafe</script>',text:'Measured < 64 ms',page_number:2,document_id:'doc',paper_id:'paper'};
 const current={...report,document:{...report.document,sections:[{...section,statements:[{kind:'finding',text:'<img src=x onerror=alert(1)>',evidence_ids:['e1'],source_spans:[]}],sources:{e1:ev}}]}};
 const html=pane({report:current});
 assert.ok(!html.includes('<script>'));assert.ok(!html.includes('<img'));
 assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('/api/pilot/documents/doc/pdf#page=2'));
 assert.ok(html.includes('출처 확인'));
});
test('editing composer distinguishes report mutation from focused question and retains stop',()=>{
 const base={value:'수정',onChange:()=>{},onSend:()=>{},onStop:()=>{},busy:false,canSend:true,hasTurns:true,focusTitle:'Retention'};
 assert.ok(renderToStaticMarkup(React.createElement(ResearchComposer,{...base,editing:true})).includes('선택 구간 수정 요청'));
 assert.ok(renderToStaticMarkup(React.createElement(ResearchComposer,base)).includes('선택 구간에 질문하기'));
 assert.ok(renderToStaticMarkup(React.createElement(ResearchComposer,{...base,editing:true,busy:true})).includes('답변 중지'));
});
