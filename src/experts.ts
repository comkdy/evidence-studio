import type { ExpertCategory, ExpertContribution, ExpertSelection, Paper } from './types';
import { normalizeSearchText } from './terminology.ts';
import { captureAnswerEvidence, selectQuestionClaims } from './answerSelection.ts';
import { completeFacets, inferFacets, knowledgeDate, knowledgeNotes, ownedPapers, paperKnowledge } from './knowledge.ts';

export const defaultExperts: ExpertCategory[] = [
  { id:'dram', name:'DRAM', scope:'Cell structure, retention, refresh와 operating conditions', keywords:'dram,refresh,retention', paperIds:['p1','p6'], enabled:true, facets:{platform:['DRAM']},reviewIds:[] },
  { id:'nand', name:'NAND', scope:'Cell structure, program/erase, endurance와 data retention', keywords:'nand,flash,program,erase,endurance', paperIds:['p2'], enabled:true, facets:{platform:['NAND']},reviewIds:[] },
  { id:'hbm', name:'HBM · Packaging', scope:'Stack structure, interconnect, bandwidth와 thermal conditions', keywords:'hbm,packaging,stack,tsv,interconnect,bandwidth,thermal', paperIds:['p3','p4'], enabled:true, facets:{platform:['HBM'],layer:['Packaging / Interconnect']},reviewIds:[] },
  { id:'reliability', name:'Reliability', scope:'Error, lifetime, degradation과 reliability test conditions', keywords:'reliability,lifetime,degradation,error,retention,thermal', paperIds:['p6','p1','p2'], enabled:true, facets:{focus:['Reliability']},reviewIds:['p3'] },
  { id:'controller', name:'Controller · ECC', scope:'Memory control, error correction과 performance evaluation', keywords:'ecc,controller,error correction,correction', paperIds:['p5'], enabled:true, facets:{layer:['Architecture / System']},reviewIds:[] },
];

// Upgrade only unchanged prototype wording; keep custom text, paper choices and status.
const previousDefaults = [
  {id:'dram',name:'DRAM',scope:'셀 구조, 보존 특성, 리프레시와 동작 조건',keywords:'dram,리프레시,refresh,보존'},
  {id:'nand',name:'NAND',scope:'셀 구조, 프로그램·소거, 내구성과 데이터 보존',keywords:'nand,낸드,flash,플래시,프로그램,소거'},
  {id:'hbm',name:'HBM · 패키징',scope:'적층 구조, 인터커넥트, 대역폭과 열 조건',keywords:'hbm,패키징,적층,tsv,인터커넥트,대역폭'},
  {id:'reliability',name:'신뢰성',scope:'오류, 수명, 열화와 신뢰성 시험 조건',keywords:'신뢰성,reliability,수명,열화,오류,retention,열 조건'},
  {id:'controller',name:'컨트롤러 · ECC',scope:'메모리 제어, 오류 정정과 성능 평가',keywords:'ecc,컨트롤러,controller,오류 정정,정정'},
];

export function updateDefaultTerminology(categories:ExpertCategory[]):ExpertCategory[] {
  return categories.map(category=>{
    const previous=previousDefaults.find(item=>item.id===category.id);
    const current=defaultExperts.find(item=>item.id===category.id);
    if(!previous||!current)return category;
    return {...category,...Object.fromEntries((['name','scope','keywords'] as const).map(key=>[key,category[key]===previous[key]?current[key]:category[key]]))};
  });
}

export function readExperts(): ExpertCategory[] {
  try {
    const current=localStorage.getItem('evidence-studio.experts.v3');
    const value:unknown = JSON.parse(current || localStorage.getItem('evidence-studio.experts.v2') || localStorage.getItem('evidence-studio.experts.v1') || 'null');
    if(Array.isArray(value) && value.length && value.every(item => item && typeof item.id==='string' && typeof item.name==='string' && typeof item.scope==='string' && typeof item.keywords==='string' && typeof item.enabled==='boolean' && Array.isArray(item.paperIds) && item.paperIds.every((id:unknown)=>typeof id==='string'))){
      return updateDefaultTerminology(value).map(item=>{
        const criteria=item.facets&&typeof item.facets==='object'&&!Array.isArray(item.facets)?Object.fromEntries(Object.entries(item.facets).filter(([,tags])=>Array.isArray(tags)&&tags.every(tag=>typeof tag==='string')&&(current||tags.length))):defaultExperts.find(expert=>expert.id===item.id)?.facets||inferFacets(`${item.name} ${item.scope}`);
        // Earlier versions treated empty arrays as unrestricted; preserve that intent.
        return {...item,facets:completeFacets(criteria),reviewIds:Array.isArray(item.reviewIds)?item.reviewIds.filter(id=>typeof id==='string'):[],excludedIds:Array.isArray(item.excludedIds)?item.excludedIds.filter(id=>typeof id==='string'):[],includedIds:Array.isArray(item.includedIds)?item.includedIds.filter(id=>typeof id==='string'):[]};
      });
    }
  } catch { /* A corrupted browser preference falls back to the prototype catalog. */ }
  return structuredClone(defaultExperts);
}

// Deterministic UI demonstration only. Production routing uses a versioned registry,
// authorized retrieval and grounded synthesis as outlined in docs/HANDOFF.md.
export function routeQuestion(question:string, categories:ExpertCategory[]) {
  const text=normalizeSearchText(question);
  return categories.filter(item=>item.enabled).map(item=>({item,score:[...new Set(item.keywords.split(',').map(normalizeSearchText))].filter(word=>word && text.includes(word)).length})).filter(result=>result.score>0).sort((a,b)=>b.score-a.score).slice(0,2).map(result=>result.item);
}

export function recommendationReason(question:string, expert:ExpertCategory) {
  const text=normalizeSearchText(question);
  const matches=[...new Set(expert.keywords.split(',').map(word=>word.trim()))].filter(word=>word&&text.includes(normalizeSearchText(word))).slice(0,3);
  return matches.length?`${matches.join(' · ')} 관련 질문을 담당합니다.`:'직접 선택한 전문가입니다.';
}

// Manual choices override routing, and unavailable categories cannot be called.
export function selectExperts(question:string, categories:ExpertCategory[], mode:ExpertSelection, selectedIds:string[]):ExpertCategory[] {
  return mode==='auto'?routeQuestion(question,categories):categories.filter(item=>item.enabled&&selectedIds.includes(item.id));
}

// Local fixture ranking only; candidates must belong to the chosen expert.
// The caller intersects explicit paper selection before calling this function.
export function rankSources(question:string, papers:Paper[], experts:ExpertCategory[], limit=3):Paper[] {
  const words=normalizeSearchText(question).split(/[\s,·]+/).filter(word=>word.length>1);
  const preferred=[...new Set(experts.filter(expert=>expert.enabled).flatMap(expert=>ownedPapers(expert,papers).map(paper=>paper.id)))];
  const platforms=inferFacets(question).platform||[];
  const ranked=papers.filter(paper=>preferred.includes(paper.id)&&(!platforms.length||platforms.some(platform=>paperKnowledge[paper.id]?.facets.platform?.includes(platform)))).map(paper=>{
    const searchable=normalizeSearchText(`${paper.title} ${paper.topic}`);
    const matches=words.filter(word=>searchable.includes(word.replace(/의$/, ''))).length;
    const priority=preferred.indexOf(paper.id);
    return {paper,score:matches*100+(priority<0?0:Math.max(1,20-priority))};
  }).sort((a,b)=>b.score-a.score);
  const relevant=ranked.filter(item=>item.score>0);
  return (relevant.length?relevant:ranked).slice(0,limit).map(item=>item.paper);
}

export function buildContributions(question:string,papers:Paper[],experts:ExpertCategory[]):ExpertContribution[] {
  return experts.filter(expert=>expert.enabled).map(expert=>{
    const candidates=rankSources(question,papers,[expert],papers.length);
    const claimMatches=selectQuestionClaims(question,candidates);
    const snapshot=captureAnswerEvidence(claimMatches);
    const sources=candidates.filter(paper=>snapshot.claims.some(claim=>claim.paperId===paper.id));
    return {id:expert.id,name:expert.name,scope:expert.scope,reason:recommendationReason(question,expert),paperIds:sources.map(paper=>paper.id),knowledgeTitles:knowledgeNotes(expert,sources).map(note=>note.title),asOf:expert.updatedAt||knowledgeDate,claimMatches,measurementIds:snapshot.measurements.map(item=>item.id)};
  });
}
