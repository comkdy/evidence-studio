import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { defaultExperts } from '../src/experts.ts';
import { previewMembership } from '../src/knowledge.ts';
import { evidenceData } from '../src/evidence.ts';
import { syncExpertWikis, reviewWikiQuestions, sharedQuestionCount, wikiChanges } from '../src/wiki.ts';

const papers=JSON.parse(await readFile(new URL('../shared/papers.json',import.meta.url),'utf8'));
const additions=[{...papers[2],id:'p7'},{...papers[1],id:'p8'}];
const initialAt='2026-10-12T00:00:00Z', updatedAt='2026-10-12T01:30:00Z';
const initial=()=>syncExpertWikis([],defaultExperts,papers,initialAt);

test('each expert Wiki contains only admitted, available evidence and profile edits do not revise it',()=>{
  const pages=initial();
  for(const page of pages){
    const expert=defaultExperts.find(item=>item.id===page.expertId);
    assert.ok(page.revisions[0].blocks.length>0);
    for(const block of page.revisions[0].blocks)assert.ok(block.paperIds.every(id=>expert.paperIds.includes(id)&&!expert.reviewIds?.includes(id)));
  }
  assert.equal(syncExpertWikis(pages,defaultExperts.map(expert=>({...expert,scope:'소개만 수정'})),papers,updatedAt),pages);
  assert.deepEqual(syncExpertWikis([],[{...defaultExperts[0],reviewIds:['p1','p6']}],papers,initialAt),[]);
  assert.deepEqual(syncExpertWikis([],defaultExperts,papers.map(paper=>({...paper,status:'pending'})),initialAt),[]);
});

test('new papers revise only related pages, preserve prior content and do not create duplicate versions',()=>{
  const pages=initial();
  const experts=defaultExperts.map(expert=>({...expert,paperIds:[...expert.paperIds,...previewMembership(expert.facets,additions,expert).paperIds]}));
  const revised=syncExpertWikis(pages,experts,[...papers,...additions],updatedAt);
  const thermal=revised.find(page=>page.id==='hbm:hbm-thermal');
  assert.equal(thermal.revisions.length,2);
  assert.deepEqual(thermal.revisions[0].paperIds,['p3']);
  assert.deepEqual(thermal.revisions[1].paperIds,['p3','p7']);
  assert.deepEqual(thermal.revisions[1].addedPaperIds,['p7']);
  assert.equal(thermal.revisions[1].reason,'새 논문 반영');
  assert.equal(thermal.revisions[1].at,updatedAt);
  for(const id of ['dram:dram-retention','hbm:hbm-interconnect','controller:controller-ecc'])assert.equal(revised.find(page=>page.id===id),pages.find(page=>page.id===id));
  assert.ok(wikiChanges(thermal.revisions[1],thermal.revisions[0]).added.every(block=>block.paperIds.includes('p7')));
  assert.equal(syncExpertWikis(revised,experts,[...papers,...additions],updatedAt),revised);
});

test('frequent-question review adds a sourced FAQ without inventing new evidence or revising on a no-change check',()=>{
  const page=initial().find(page=>page.id==='hbm:hbm-thermal');
  const question={question:'HBM thermal 조건은?',visibility:'team',deleted:false};
  const baseline=sharedQuestionCount(page,[]);
  assert.equal(sharedQuestionCount(page,[question,{...question,visibility:'private'},{...question,deleted:true},{...question,question:'DRAM retention 조건은?'}]),baseline+1);
  const reviewed=reviewWikiQuestions(page,updatedAt,baseline+1,papers);
  assert.equal(reviewed.revisions.length,2);
  assert.deepEqual(reviewed.revisions[1].paperIds,page.revisions[0].paperIds);
  assert.deepEqual(reviewed.revisions[1].addedPaperIds,[]);
  const changes=wikiChanges(reviewed.revisions[1],reviewed.revisions[0]);
  assert.equal(changes.added.length,1);
  assert.equal(changes.added[0].kind,'faq');
  assert.deepEqual(changes.added[0].paperIds,['p3']);
  const checked=reviewWikiQuestions(reviewed,'2026-10-12T02:00:00Z',baseline+2,papers);
  assert.equal(checked.revisions,reviewed.revisions);
  assert.equal(checked.lastCheckedAt,'2026-10-12T02:00:00Z');
});

test('unverified new summaries stay outside FAQ and excluded papers leave the current Wiki',()=>{
  const page=reviewWikiQuestions(initial().find(page=>page.id==='hbm:hbm-thermal'),updatedAt,12,papers);
  const expert={...defaultExperts[2],paperIds:['p3','p4','p7']};
  const next=syncExpertWikis([page],[expert],[...papers,...additions],'2026-10-12T02:00:00Z').find(item=>item.id===page.id);
  assert.equal(next.revisions.length,3);
  assert.ok(!next.revisions[2].blocks.find(block=>block.kind==='faq').paperIds.includes('p7'));
  assert.ok(next.revisions[2].blocks.some(block=>block.paperIds.includes('p7')&&!block.claimIds.length));
  const narrowed=syncExpertWikis([next],[{...expert,excludedIds:['p3']}],[...papers,...additions],'2026-10-12T03:00:00Z').find(item=>item.id===page.id);
  assert.deepEqual(narrowed.revisions.at(-1).paperIds,['p7']);
  assert.ok(narrowed.revisions.at(-1).blocks.every(block=>!block.paperIds.includes('p3')));
  assert.deepEqual(narrowed.revisions.at(-1).removedPaperIds,['p3']);
  assert.deepEqual(page.revisions[0].paperIds,['p3']);
});

test('Wiki reuses canonical claims, linked measurements and evidence instead of independently rewriting results',()=>{
  for(const page of initial()){
    for(const block of page.revisions[0].blocks){
      for(const id of block.claimIds){
        const claim=evidenceData.claims.find(item=>item.id===id);
        assert.ok(claim&&block.paperIds.includes(claim.paperId)&&claim.validation==='passed');
        if(block.kind==='knowledge')assert.equal(claim.kind,'result');
      }
      for(const id of block.measurementIds){
        const measure=evidenceData.measurements.find(item=>item.id===id);
        assert.ok(measure&&block.paperIds.includes(measure.paperId));
        assert.ok(block.claimIds.some(claimId=>evidenceData.claims.find(item=>item.id===claimId).measurementIds.includes(id)));
      }
      assert.ok(block.evidenceIds.every(id=>evidenceData.spans.some(span=>span.id===id&&block.paperIds.includes(span.paperId))));
    }
  }
  const empty=syncExpertWikis([],[{...defaultExperts[2],paperIds:['p7']}],additions,initialAt)[0];
  assert.equal(reviewWikiQuestions(empty,updatedAt,15,additions).revisions.length,1);
});

test('knowledge explains findings while uncertainty is separate and grounded in its own claim',()=>{
  const dram=initial().find(page=>page.id==='dram:dram-retention').revisions[0];
  const knowledge=dram.blocks.find(block=>block.kind==='knowledge');
  assert.match(knowledge.title,/시료.*온도/);
  assert.match(knowledge.text,/120 ms/);assert.match(knowledge.text,/96 ms/);
  assert.match(knowledge.text,/판단할 수 없/);
  assert.deepEqual(knowledge.claimIds,['p1-result','p6-result']);
  const refresh=dram.blocks.find(block=>block.claimIds.includes('p1-interpretation'));
  assert.equal(refresh.kind,'gap');assert.match(refresh.title,/확정할 수 없다/);
  assert.deepEqual(refresh.measurementIds,[]);
  assert.deepEqual(refresh.claimIds,['p1-interpretation']);
  for(const page of initial())for(const block of page.revisions[0].blocks){
    assert.ok(!['실험 결과','저자의 해석','가설','한계'].includes(block.title));
    assert.ok(!block.claimIds.includes('p5-interpretation'));
  }
  const pending=syncExpertWikis([],[{...defaultExperts[2],paperIds:['p7']}],additions,initialAt)[0];
  assert.ok(pending.revisions[0].blocks.every(block=>block.kind==='pending'));
});

test('narrowing a Wiki keeps only allowed facts and corrections create a new knowledge revision',()=>{
  const expert={...defaultExperts[0],paperIds:['p1']};
  const pages=syncExpertWikis([],[expert],papers,initialAt);
  const body=pages[0].revisions[0].blocks.find(block=>block.kind==='knowledge');
  assert.deepEqual(body.claimIds,['p1-result']);
  assert.doesNotMatch(body.text,/96 ms|DRAM-B/);
  const claim=evidenceData.claims.find(item=>item.id==='p1-result');
  const before=claim.summary;
  try{
    claim.summary=before+' 정정된 적용 범위 예시.';
    const updated=syncExpertWikis(pages,[expert],papers,updatedAt)[0];
    assert.equal(updated.revisions.length,2);
    assert.equal(updated.revisions[0].blocks.find(block=>block.kind==='knowledge').text,body.text);
    const changes=wikiChanges(updated.revisions[1],updated.revisions[0]);
    assert.ok(changes.edited.some(change=>change.after.text.includes('정정된 적용 범위')));
  }finally{claim.summary=before;}
});

test('a growing topic splits its findings into bounded knowledge points with complete links',()=>{
  const base=evidenceData.claims.find(item=>item.id==='p1-result');
  const count=evidenceData.claims.length;
  try{
    evidenceData.claims.push(...Array.from({length:10},(_,i)=>({...structuredClone(base),id:'large-topic-'+i,summary:'추가 관찰 '+i+': '+base.summary})));
    const page=initial().find(item=>item.id==='dram:dram-retention');
    const blocks=page.revisions[0].blocks.filter(block=>block.kind==='knowledge');
    assert.equal(blocks.length,4);
    assert.ok(blocks.every(block=>block.claimIds.length<=3));
    assert.equal(new Set(blocks.flatMap(block=>block.claimIds)).size,12);
    assert.ok(blocks.every(block=>block.paperIds.every(id=>['p1','p6'].includes(id))));
  }finally{evidenceData.claims.splice(count);}
});
