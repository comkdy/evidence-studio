import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import * as pilotData from '../src/pilotData.ts';
import * as wikiUsage from '../src/wikiUsage.ts';

const require=createRequire(import.meta.url);
const source=fs.readFileSync(new URL('../src/WikiJobActivity.tsx',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
const component={exports:{}};
new Function('require','module','exports',compiled)(name=>name==='./pilotData'?pilotData:name==='./wikiUsage'?wikiUsage:require(name),component,component.exports);
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
