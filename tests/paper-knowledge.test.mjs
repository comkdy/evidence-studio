import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const require=createRequire(import.meta.url),module={exports:{}};
const wikiCard=props=>React.createElement('div',{'data-wiki':props.wiki.id},props.wiki.title);
const localRequire=name=>name==='./PilotViews'?{WikiCard:wikiCard}:name.endsWith('.css')?{}:require(name);
new Function('require','module','exports',ts.transpileModule(fs.readFileSync(new URL('../src/PaperKnowledge.tsx',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText)(localRequire,module,module.exports);
const {PaperKnowledge}=module.exports;
const knowledge={coverage:{status:'ready',ready_chunks:2,total_chunks:2,review_state:'model_reviewed_human_pending'},common_wikis:[{id:'source-new',title:'Source evidence'}],experts:[{id:'dram',name:'DRAM',enabled:true}]};
test('demand source readiness is visible without claiming canonical claims or human verification',()=>{
 const html=renderToStaticMarkup(React.createElement(PaperKnowledge,{knowledge,onExpert(){},onPaper(){},revision:1}));
 for(const value of ['2 / 2','원문 근거 준비됨','사람 검토 대기','구조화된 주장·수치 목록은 별도로','data-wiki="source-new"','DRAM의 지식 보기'])assert.ok(html.includes(value),value);
});
test('expert button passes the exact owner ID and disabled experts are labeled',()=>{
 let selected='';const tree=PaperKnowledge({knowledge:{...knowledge,experts:[{id:'paused',name:'Paused',enabled:false}]},onExpert:id=>selected=id,onPaper(){},revision:1});
 function find(root){if(root?.type==='button')return root;for(const child of React.Children.toArray(root?.props?.children)){const value=find(child);if(value)return value;}}
 find(tree).props.onClick();assert.equal(selected,'paused');assert.ok(renderToStaticMarkup(tree).includes('사용 중지'));
});
test('pending coverage describes missing prepared source notes, not completed paper analysis',()=>{
 const html=renderToStaticMarkup(React.createElement(PaperKnowledge,{knowledge:{coverage:{status:'pending',ready_chunks:0,total_chunks:2,review_state:'pending'},common_wikis:[],experts:[]},onExpert(){},onPaper(){},revision:1}));
 assert.ok(html.includes('원문 근거 준비 대기'));assert.ok(html.includes('공통 Wiki가 없습니다.'));assert.ok(!html.includes('모델 검토 완료'));
});
