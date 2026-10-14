import test from 'node:test';
import assert from 'node:assert/strict';
import {tokenLabel,durationLabel,callLabel} from '../src/wikiUsage.ts';

test('missing tokens remain unknown while genuine zero usage stays zero',()=>{
 assert.equal(tokenLabel(null,0,2),'미제공');
 assert.equal(tokenLabel(0,2,2),'0');
 assert.equal(tokenLabel(0,0,0),'0');
 assert.equal(tokenLabel(12000,1,2),'12,000 (일부)');
 assert.equal(tokenLabel(0,1,2),'0 (일부)');
});
test('elapsed time distinguishes sub-second work and long running jobs',()=>{
 assert.equal(durationLabel(0),'0초');
 assert.equal(durationLabel(500),'1초 미만');
 assert.equal(durationLabel(90500),'1분 30초');
 assert.equal(durationLabel(7321000),'2시간 2분 1초');
 assert.equal(durationLabel(-1),'기록 없음');
});
test('failed transmitted calls and requests still waiting for a slot are distinct',()=>{
 assert.equal(callLabel({calls:3,running:1,failed:1,interrupted:0,waiting:1,not_sent:0}),'3회 · 진행 1 · 실패 1 · 호출 대기 1');
 assert.equal(callLabel({calls:0,running:0,failed:0,interrupted:0,waiting:0,not_sent:1}),'0회 · 미전송 1');
});
