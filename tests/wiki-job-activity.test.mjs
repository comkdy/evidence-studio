import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import * as pilotData from '../src/pilotData.ts';
import * as wikiUsage from '../src/wikiUsage.ts';
import * as wikiRetry from '../src/wikiRetry.ts';

const require=createRequire(import.meta.url);
const source=fs.readFileSync(new URL('../src/WikiJobActivity.tsx',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
const component={exports:{}};
new Function('require','module','exports',compiled)(name=>name==='./pilotData'?pilotData:name==='./wikiUsage'?wikiUsage:name==='./wikiRetry'?wikiRetry:require(name),component,component.exports);
const {WikiJobActivity}=component.exports;
const job={id:1,scope:'common',label:'공통 Wiki 갱신',state:'failed',created_at:'2026-10-15T00:00:00Z',finished_at:null,usage:{available:false,models:[]}};

test('failed Wiki job shows reason and source location with escaped text',()=>{
 const error='인용 위치 중복 [quote_ambiguous]\n위치: paper p1 · p.3 · 근거 a <script>bad()</script>';
 const html=renderToStaticMarkup(React.createElement(WikiJobActivity,{jobs:[{...job,error}]}));
 assert.ok(html.includes('quote_ambiguous'));
 assert.ok(html.includes('p.3'));
 assert.ok(html.includes('wiki-job-error'));
 assert.ok(html.includes('&lt;script&gt;'));
 assert.ok(!html.includes('<script>'));
});

test('legacy jobs without error remain renderable',()=>{
 const html=renderToStaticMarkup(React.createElement(WikiJobActivity,{jobs:[job]}));
 assert.ok(!html.includes('wiki-job-error'));
 assert.ok(!html.includes('undefined'));
});

test('retry button is visible only to permitted users for failed jobs',()=>{
 for(const state of ['queued','running','done','failed']){
  for(const canRetry of [false,true]){
   const html=renderToStaticMarkup(React.createElement(WikiJobActivity,{jobs:[{...job,state}],canRetry}));
   assert.equal(html.includes('aria-label="Wiki 작업 #1 재시도"'),canRetry&&state==='failed');
  }
 }
});

test('retry sends the selected job and observed failure without a full refresh request',async()=>{
 const original=globalThis.fetch,calls=[];
 globalThis.fetch=async(path,init)=>{
  calls.push({path,init});
  return new Response(JSON.stringify({status:'queued',job_id:7,already_requested:false}),{status:202});
 };
 try{
  await wikiRetry.retryWikiJob({...job,id:7,finished_at:'2026-10-15T01:00:00Z'});
  assert.equal(calls[0].path,'/api/pilot/wiki-maintenance/jobs/7/retry');
  assert.equal(calls[0].init.method,'POST');
  assert.deepEqual(JSON.parse(calls[0].init.body),{failed_at:'2026-10-15T01:00:00Z'});
  for(const state of ['done','running','queued'])await assert.rejects(wikiRetry.retryWikiJob({...job,state}));
  assert.equal(calls.length,1);
 }finally{globalThis.fetch=original;}
});

test('retry API error reaches the caller for display',async()=>{
 const original=globalThis.fetch;
 globalThis.fetch=async()=>new Response(JSON.stringify({detail:'최신 실패 내역을 확인하세요.'}),{status:409});
 try{await assert.rejects(wikiRetry.retryWikiJob(job),/최신 실패/);}
 finally{globalThis.fetch=original;}
});
