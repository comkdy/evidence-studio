import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { rankSources, routeQuestion, selectExperts, defaultExperts, updateDefaultTerminology, buildContributions, readExperts } from '../src/experts.ts';
import { completeFacets, facets, inferFacets, previewMembership, knowledgeNotes } from '../src/knowledge.ts';
import { canManageExperts, canManageUsers, canManageKnowledge, resolveDemoRole } from '../src/access.ts';
import { normalizeSearchText } from '../src/terminology.ts';
import { evidenceData, getAnswerClaims, getClaims } from '../src/evidence.ts';

const papers=JSON.parse(await readFile(new URL('../shared/papers.json',import.meta.url),'utf8'));

test('manual expert selection overrides automatic routing and ignores unavailable experts',()=>{
  const categories=defaultExperts.map(item=>({...item,enabled:item.id!=='nand'}));
  assert.deepEqual(selectExperts('DRAM',categories,'manual',['hbm','controller','nand','removed']).map(item=>item.id),['hbm','controller']);
  assert.deepEqual(selectExperts('DRAM',categories,'manual',[]),[]);
  assert.deepEqual(selectExperts('DRAM',categories,'auto',['hbm']),routeQuestion('DRAM',categories));
});

test('admins manage users, admins and superusers manage experts and knowledge',()=>{
  assert.equal(canManageExperts('admin'),true);
  assert.equal(canManageExperts('superuser'),true);
  assert.equal(canManageKnowledge('superuser'),true);
  assert.equal(canManageUsers('admin'),true);
  assert.equal(canManageUsers('superuser'),false);
  for(const role of ['editor','member','관리자','',null,undefined])assert.equal(canManageExperts(role),false);
  assert.equal(resolveDemoRole(undefined),'admin');
  assert.equal(resolveDemoRole('superuser'),'superuser');
  assert.equal(resolveDemoRole('unknown'),'user');
});

test('Korean aliases retrieve English terms without merging distinct concepts',()=>{
  assert.equal(normalizeSearchText('보존 시간과 리프레시 간격'),'retention time과 refresh interval');
  assert.equal(normalizeSearchText('프로그램·소거'),'program/erase');
  assert.notEqual(normalizeSearchText('보존'),normalizeSearchText('내구성'));
  assert.equal(normalizeSearchText('VDD'),normalizeSearchText('공급 전압'));
  assert.equal(normalizeSearchText('VDDQ'),'vddq');
  for(const [ko,en] of [['리프레시','refresh'],['대역폭','bandwidth'],['오류 정정','error correction']])assert.deepEqual(routeQuestion(ko,defaultExperts),routeQuestion(en,defaultExperts));
  assert.ok(papers.some(paper=>normalizeSearchText(paper.abstract).includes(normalizeSearchText('신뢰성'))));
});

test('updating terminology preserves custom expert settings',()=>{
  const saved={...defaultExperts[2],name:'HBM · 패키징',scope:'내가 정한 담당 범위',keywords:'사용자키워드',paperIds:['p7'],enabled:false};
  const [updated]=updateDefaultTerminology([saved]);
  assert.equal(updated.name,'HBM · Packaging');
  assert.equal(updated.scope,saved.scope);assert.equal(updated.keywords,saved.keywords);
  assert.deepEqual(updated.paperIds,['p7']);assert.equal(updated.enabled,false);
  assert.equal(saved.name,'HBM · 패키징');
  assert.equal(updateDefaultTerminology([{...saved,name:'사용자 전문가'}])[0].name,'사용자 전문가');
});

test('claims and measurements keep exact, versioned, same-paper evidence',()=>{
  for(const collection of Object.values(evidenceData)) assert.equal(new Set(collection.map(item=>item.id)).size,collection.length);
  for(const span of evidenceData.spans){
    const paper=papers.find(item=>item.id===span.paperId);
    assert.ok(paper&&span.page>0&&span.page<=paper.pages);
    assert.ok(span.documentVersion&&span.extractionRunId&&span.quote);
  }
  for(const claim of evidenceData.claims){
    for(const link of claim.evidence){
      const span=evidenceData.spans.find(item=>item.id===link.spanId);
      assert.equal(span?.paperId,claim.paperId);
      if(link.role==='statement')assert.equal(span.quote,claim.rawText);
    }
    assert.ok(claim.evidence.some(link=>link.role==='statement'));
    if(claim.hasDirectSupport)assert.ok(claim.evidence.some(link=>link.role==='support'));
    for(const id of claim.measurementIds)assert.equal(evidenceData.measurements.find(item=>item.id===id)?.paperId,claim.paperId);
  }
  for(const measurement of evidenceData.measurements){
    for(const id of [...measurement.evidenceIds,...measurement.conditions.flatMap(item=>item.evidenceIds)])assert.equal(evidenceData.spans.find(item=>item.id===id)?.paperId,measurement.paperId);
    for(const condition of measurement.conditions)assert.equal(condition.rawValue!==null,condition.evidenceIds.length>0);
  }
  for(const relation of evidenceData.relations){
    assert.ok(evidenceData.claims.some(item=>item.id===relation.from));
    assert.ok(evidenceData.claims.some(item=>item.id===relation.to));
  }
});

test('inequalities, unit conversion and unknown conditions survive in fixtures',()=>{
  const nand=evidenceData.measurements.find(item=>item.paperId==='p2');
  assert.equal(nand.operator,'>=');assert.equal(nand.value,3000);assert.match(nand.rawValue,/≥/);
  const dram=evidenceData.measurements.find(item=>item.paperId==='p1');
  assert.equal(dram.value/1000,dram.normalizedValue);assert.equal(dram.normalizedUnit,'s');
  const hbm=evidenceData.measurements.find(item=>item.paperId==='p3');
  assert.ok(Math.abs(hbm.value+273.15-hbm.normalizedValue)<1e-9);
  assert.ok(evidenceData.measurements.find(item=>item.paperId==='p5').conditions.some(item=>item.name==='Supply voltage'&&item.rawValue===null));
  assert.equal(evidenceData.relations[0].comparisonStatus,'different-conditions');
});

test('unverified hypotheses are visible in detail but excluded from answers',()=>{
  assert.ok(getClaims('p5').some(item=>item.validation==='needs-review'));
  assert.ok(getAnswerClaims('p5').every(item=>item.validation==='passed'));
  assert.ok(!getAnswerClaims('p5').some(item=>item.id==='p5-interpretation'));
  assert.deepEqual(getAnswerClaims('p7'),[]);
});

test('answers stay within admitted expert papers, even when outside papers match better',()=>{
  const newPaper={...papers[2],id:'p7',title:'Stack-Level Thermal Analysis for HBM'};
  const routed=routeQuestion('HBM',defaultExperts);
  assert.ok(!routed.some(expert=>expert.paperIds.includes('p7')));
  assert.ok(!rankSources('HBM',[...papers,newPaper],routed).some(paper=>paper.id==='p7'));
  assert.deepEqual(rankSources('HBM',[newPaper],routed),[]);
  assert.deepEqual(rankSources('DRAM',papers,routeQuestion('DRAM',defaultExperts)).map(item=>item.id),['p1','p6']);
  assert.deepEqual(routeQuestion('DRAM',defaultExperts.map(item=>({...item,enabled:false}))),[]);
  assert.deepEqual(rankSources('NAND',papers,[{...defaultExperts[1],paperIds:[]}]),[]);
  assert.deepEqual(rankSources('HBM',papers,[]),[]);
  assert.deepEqual(rankSources('HBM',papers,[{...defaultExperts[2],enabled:false}]),[]);
  assert.deepEqual(rankSources('HBM',papers.map(paper=>({...paper,status:'pending'})),routed),[]);
  assert.deepEqual(rankSources('HBM',papers,[{...defaultExperts[2],reviewIds:['p3'],excludedIds:['p4']}]),[]);
});

test('classification combines facets and keeps ambiguous papers for review, not knowledge',()=>{
  const criteria=inferFacets('DRAM Reliability');
  const membership=previewMembership(criteria,papers);
  assert.deepEqual(membership.paperIds,['p1','p6']);
  assert.deepEqual(membership.reviewIds,['p5']);
  const expert={...defaultExperts[0],...membership,facets:criteria};
  assert.deepEqual(knowledgeNotes(expert,papers).map(note=>note.paper.id),['p1','p6']);
  assert.deepEqual(previewMembership({platform:['MRAM']},papers),{paperIds:[],reviewIds:[]});
  assert.deepEqual(previewMembership({},papers),{paperIds:papers.map(paper=>paper.id),reviewIds:[]});
  assert.deepEqual(previewMembership(criteria,papers.map(paper=>({...paper,status:'pending'}))),{paperIds:[],reviewIds:[]});
  assert.deepEqual(previewMembership(criteria,papers,{includedIds:['p6'],excludedIds:['p1','p5']}),{paperIds:['p6'],reviewIds:[]});
});

test('all-selected facets are unrestricted, while deselected axes admit no automatic papers',()=>{
  const all=completeFacets();
  for(const facet of facets)assert.deepEqual(all[facet.key],facet.values);
  assert.deepEqual(previewMembership(all,papers),{paperIds:papers.map(paper=>paper.id),reviewIds:[]});
  for(const facet of facets)assert.deepEqual(previewMembership({...all,[facet.key]:[]},papers),{paperIds:[],reviewIds:[]});
  assert.deepEqual(previewMembership({...all,method:[]},papers,{includedIds:['p1'],excludedIds:['p2']}),{paperIds:['p1'],reviewIds:[]});
  assert.deepEqual(previewMembership(all,papers.map(paper=>({...paper,status:'pending'}))),{paperIds:[],reviewIds:[]});
});

test('memory alternatives use OR and research focus and detail selections use AND',()=>{
  const selected={...completeFacets(),platform:['DRAM','NAND'],focus:['Reliability']};
  assert.deepEqual(previewMembership(selected,papers),{paperIds:['p1','p2','p6'],reviewIds:['p5']});
  assert.deepEqual(previewMembership({...selected,method:['Experiment']},papers),{paperIds:['p1','p2'],reviewIds:['p5']});
  assert.deepEqual(previewMembership({...selected,application:['CIM / PIM']},papers),{paperIds:[],reviewIds:['p1','p2','p5','p6']});
});

test('legacy empty filters remain unrestricted but new empty selections survive reload',()=>{
  const saved={...defaultExperts[0],scope:'관리자가 작성한 전문가 개요',facets:{platform:['DRAM'],layer:[]}};
  const original=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
  try{
    const values=new Map([['evidence-studio.experts.v2',JSON.stringify([saved])]]);
    Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:key=>values.get(key)||null}});
    const legacy=readExperts()[0];
    assert.deepEqual(legacy.facets.platform,['DRAM']);
    assert.deepEqual(legacy.facets.layer,facets.find(facet=>facet.key==='layer').values);
    assert.equal(legacy.scope,saved.scope);
    assert.deepEqual(legacy.paperIds,saved.paperIds);
    values.set('evidence-studio.experts.v3',JSON.stringify([saved]));
    assert.deepEqual(readExperts()[0].facets.layer,[]);
  }finally{
    if(original)Object.defineProperty(globalThis,'localStorage',original);else delete globalThis.localStorage;
  }
});

test('multiple experts retain separate evidence and never widen explicit paper selection',()=>{
  const experts=[defaultExperts[0],defaultExperts[2]];
  const contributions=buildContributions('DRAM과 HBM 비교',papers,experts);
  for(const contribution of contributions){
    const expert=experts.find(item=>item.id===contribution.id);
    assert.ok(contribution.paperIds.every(id=>expert.paperIds.includes(id)));
    assert.equal(contribution.knowledgeTitles.length,contribution.paperIds.length);
  }
  const selected=buildContributions('HBM',papers.filter(paper=>paper.id==='p3'),experts);
  assert.deepEqual(selected.map(item=>item.paperIds),[[],['p3']]);
  const oldName=contributions[0].name;
  const updated={...experts[0],name:'새 이름',paperIds:[]};
  assert.deepEqual(buildContributions('DRAM',papers,[updated])[0].paperIds,[]);
  assert.equal(contributions[0].name,oldName);
  assert.ok(contributions[0].paperIds.length>0);
});
