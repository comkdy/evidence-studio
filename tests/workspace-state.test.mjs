import test from 'node:test';
import assert from 'node:assert/strict';
import { boundedPage, reconcileExpertScope } from '../src/workspaceState.ts';

test('scope refresh removes deleted and inactive experts from new questions while retaining inactive paper browsing',()=>{
 const experts=[{id:'dram',enabled:1},{id:'nor',enabled:0},{id:'modeling'}];
 assert.deepEqual(reconcileExpertScope(experts,['dram','nor','deleted','dram','modeling'],'nor'),{
  selectedIds:['dram','modeling'],filterId:'nor',
 });
 assert.equal(reconcileExpertScope(experts,[],'deleted').filterId,'');
});

test('restoring an expert permits a fresh selection without changing a historical selection snapshot',()=>{
 const historical=['dram','nor'];
 const state=reconcileExpertScope([{id:'dram',enabled:1}],historical,'nor');
 assert.deepEqual(historical,['dram','nor']);
 assert.deepEqual(state,{selectedIds:['dram'],filterId:''});
 const restored=reconcileExpertScope([{id:'dram',enabled:1},{id:'nor',enabled:1}],historical,'nor');
 assert.deepEqual(restored,{selectedIds:historical,filterId:'nor'});
});

test('membership and corpus changes clamp list and evidence pages to remaining results',()=>{
 assert.equal(boundedPage(3,8,8),0);
 assert.equal(boundedPage(2,0,6),0);
 assert.equal(boundedPage(2,4,3),1);
 assert.equal(boundedPage(1,17,8),1);
 assert.equal(boundedPage(-1,17,8),0);
});
