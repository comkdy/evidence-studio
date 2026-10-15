import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const require=createRequire(import.meta.url),component={exports:{}};
const source=fs.readFileSync(new URL('../src/ResearchComposer.tsx',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
new Function('require','module','exports',compiled)(require,component,component.exports);
const {ResearchComposer}=component.exports;
const defaults={value:'',onChange:()=>{},onSend:()=>{},onStop:()=>{},busy:false,canSend:true,hasTurns:false};
function element(root,type){
 if(root?.type===type)return root;
 for(const child of React.Children.toArray(root?.props?.children)){const match=element(child,type);if(match)return match;}
}

test('first conversation visibly uses a multiline message composer',()=>{
 const html=renderToStaticMarkup(React.createElement(ResearchComposer,defaults));
 assert.ok(html.includes('<textarea'));
 assert.ok(/maxlength="1000"/i.test(html));
 assert.ok(html.includes('대화 시작'));
 assert.ok(html.includes('Shift+Enter'));
 assert.ok(element(ResearchComposer(defaults),'button').props.disabled);
});

test('followup keeps the composer ready for another message',()=>{
 const html=renderToStaticMarkup(React.createElement(ResearchComposer,{...defaults,value:'그 조건은?',hasTurns:true}));
 assert.ok(html.includes('이어서 대화하기'));
 assert.ok(html.includes('이어서 질문'));
 assert.ok(!element(ResearchComposer({...defaults,value:'그 조건은?',hasTurns:true}),'button').props.disabled);
});

test('Enter sends but newline and Korean IME composition do not submit',()=>{
 let sent=0,prevented=0;
 const textarea=element(ResearchComposer({...defaults,value:'DRAM 조건',onSend:()=>sent++}),'textarea');
 const fire=(override={})=>textarea.props.onKeyDown({key:'Enter',shiftKey:false,nativeEvent:{isComposing:false,keyCode:13},preventDefault:()=>prevented++,...override});
 fire({shiftKey:true});fire({nativeEvent:{isComposing:true,keyCode:13}});fire({nativeEvent:{isComposing:false,keyCode:229}});
 assert.equal(sent,0);assert.equal(prevented,0);
 fire();assert.equal(sent,1);assert.equal(prevented,1);
});

test('busy, unavailable and empty input cannot send from keyboard or form',()=>{
 let sent=0;
 for(const overrides of [{busy:true,value:'next'},{canSend:false,value:'next'},{value:'  '}]){
  const form=ResearchComposer({...defaults,...overrides,onSend:()=>sent++});
  element(form,'textarea').props.onKeyDown({key:'Enter',shiftKey:false,nativeEvent:{isComposing:false,keyCode:13},preventDefault:()=>{}});
  form.props.onSubmit({preventDefault:()=>{}});
 }
 assert.equal(sent,0);
});

test('busy composer exposes a stop control while preserving the next draft',()=>{
 let stopped=0,draft='';
 const form=ResearchComposer({...defaults,busy:true,value:'다음 질문',onStop:()=>stopped++,onChange:value=>draft=value});
 const button=element(form,'button');
 assert.equal(button.props.type,'button');
 button.props.onClick();assert.equal(stopped,1);
 element(form,'textarea').props.onChange({target:{value:'다음 질문 수정'}});
 assert.equal(draft,'다음 질문 수정');
 assert.ok(renderToStaticMarkup(form).includes('답변 중지'));
});
