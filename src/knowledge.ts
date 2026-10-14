import type { ExpertCategory, ExpertFacets, Facet, Paper } from './types';
import { getAnswerClaims, getClaimMeasurements, kindLabels } from './evidence.ts';

// Small, authored fixtures for the interaction prototype. No document analysis.
export const knowledgeDate='2026-10-12';
export const facets: {key:Facet;label:string;values:string[]}[] = [
  {key:'platform',label:'메모리 종류',values:['DRAM','NAND','SRAM','HBM','MRAM','RRAM','PCM','Ferroelectric Memory']},
  {key:'focus',label:'연구 초점',values:['Reliability','Performance','Scaling','Mechanism']},
  {key:'layer',label:'연구 계층',values:['Device / Cell','Circuit / Array','Process / Integration','Packaging / Interconnect','Architecture / System','Material / Physics']},
  {key:'application',label:'응용 방식',values:['CIM / PIM','Neuromorphic','CAM']},
  {key:'method',label:'연구 방식',values:['Experiment','Simulation','Review']},
];

// An omitted axis is unrestricted. An explicit empty selection admits nothing.
export function completeFacets(criteria:ExpertFacets={}):ExpertFacets {
  return Object.fromEntries(facets.map(facet=>[facet.key,[...(criteria[facet.key]??facet.values)]]));
}
export function restrictedFacets(criteria:ExpertFacets={}) {
  return facets.filter(facet=>criteria[facet.key]!==undefined&&!facet.values.every(value=>criteria[facet.key]!.includes(value)));
}
type PaperKnowledge = {facets:ExpertFacets; title:string; summary?:string; issue:string; gap:string; reason:string};
export const paperKnowledge:Record<string,PaperKnowledge> = {
  p1:{facets:{platform:['DRAM'],focus:['Reliability'],layer:['Device / Cell'],method:['Experiment']},title:'Retention과 refresh 조건',issue:'서로 다른 온도에서 측정한 retention을 직접 비교할 수 있을까요?',gap:'동일 온도·시료 조건에서의 비교 근거가 더 필요합니다.',reason:'담당 분야의 retention·refresh 조건을 구체적으로 확인할 수 있는 예시입니다.'},
  p2:{facets:{platform:['NAND'],focus:['Reliability','Mechanism'],layer:['Device / Cell'],method:['Experiment']},title:'Endurance와 저자의 mechanism 해석',issue:'Endurance 결과가 mechanism 설명까지 뒷받침하는지 확인해야 합니다.',gap:'제안된 mechanism의 직접 실험 검증이 충분하지 않습니다.',reason:'Endurance 결과와 해석의 근거 강도를 구분하는 데 도움이 됩니다.'},
  p3:{facets:{platform:['HBM'],layer:['Packaging / Interconnect'],method:['Simulation']},title:'Stacked HBM의 thermal conditions',issue:'Stack 구조에 따른 thermal 조건 차이가 성능 비교에 영향을 줍니다.',gap:'동일한 operating conditions에서의 비교가 더 필요합니다.',reason:'HBM 구조와 thermal 조건을 함께 다루는 담당 논문입니다.'},
  p4:{facets:{platform:['HBM'],focus:['Performance'],layer:['Packaging / Interconnect'],method:['Simulation']},title:'Interconnect와 bandwidth 비교',issue:'Bandwidth를 비교하려면 signal conditions를 먼저 맞춰야 합니다.',gap:'다른 구조의 bandwidth를 동일 조건으로 비교한 자료가 부족합니다.',reason:'Interconnect 관점에서 HBM의 비교 조건을 보완합니다.'},
  p5:{facets:{focus:['Reliability','Performance'],layer:['Architecture / System'],method:['Experiment']},title:'ECC와 평가 조건',issue:'ECC 성능을 비교하기 전에 error model과 누락 조건을 점검합니다.',gap:'Supply voltage가 미기재이며 일부 가설은 원문 대조가 필요합니다.',reason:'ECC 정책의 적용 범위와 확인할 조건을 함께 볼 수 있습니다.'},
  p6:{facets:{platform:['DRAM'],focus:['Reliability'],method:['Review']},title:'Reliability test의 비교 가능성',issue:'서로 다른 test conditions의 결과를 하나의 우열로 묶지 않습니다.',gap:'다른 메모리 종류로 일반화할 근거가 더 필요합니다.',reason:'기반 논문으로서 reliability 비교의 기준을 제공합니다.'},
  p7:{facets:{platform:['HBM'],layer:['Packaging / Interconnect'],method:['Simulation']},title:'Stack thermal 분석의 추가 자료',summary:'새로 들어온 thermal 문헌의 요약 카드 예시입니다. 상세 주장 근거는 아직 연결되지 않았습니다.',issue:'새 자료가 기존 thermal 비교를 보완하는지 확인할 수 있습니다.',gap:'상세 주장과 수치·조건 근거가 아직 없습니다.',reason:'최근 등록된 추가 자료입니다. 학계의 화제성을 뜻하지 않습니다.'},
  p8:{facets:{platform:['NAND'],focus:['Reliability'],layer:['Device / Cell']},title:'Multilevel NAND retention window',summary:'NAND retention 관련 추가 자료 카드입니다. 상세 주장 근거는 아직 연결되지 않았습니다.',issue:'Retention window의 적용 조건을 추가 확인해야 합니다.',gap:'상세 주장과 측정 조건 근거가 아직 없습니다.',reason:'기존 NAND 문헌을 보완할 추가 자료입니다.'},
};

export function inferFacets(text:string):ExpertFacets {
  const normalized=text.toLowerCase();
  const result:ExpertFacets={};
  for(const facet of facets){
    const values=facet.values.filter(value=>value.toLowerCase().split(' / ').some(word=>normalized.includes(word)));
    if(values.length)result[facet.key]=values;
  }
  if(/retention|refresh|endurance|신뢰성|보존/.test(normalized))result.focus=[...new Set([...(result.focus||[]),'Reliability'])];
  if(/패키징|tsv|bonding/.test(normalized))result.layer=['Packaging / Interconnect'];
  return result;
}

export function previewMembership(criteria:ExpertFacets,papers:Paper[],overrides:{includedIds?:string[];excludedIds?:string[]}={}) {
  const chosen=restrictedFacets(criteria).map(facet=>[facet.key,criteria[facet.key]!] as const);
  const emptySelection=chosen.some(([,values])=>!values.length);
  const paperIds:string[]=[],reviewIds:string[]=[];
  for(const paper of papers.filter(item=>item.status!=='pending')){
    if(overrides.excludedIds?.includes(paper.id))continue;
    if(overrides.includedIds?.includes(paper.id)){paperIds.push(paper.id);continue;}
    if(emptySelection)continue;
    const data=paperKnowledge[paper.id]?.facets||{};
    const matches=chosen.map(([key,values])=>!data[key]?.length?'unknown':values.some(value=>data[key]?.includes(value))?'match':'mismatch');
    if(matches.every(value=>value==='match'))paperIds.push(paper.id);
    else if(matches.includes('match')&&!matches.includes('mismatch'))reviewIds.push(paper.id);
  }
  return {paperIds,reviewIds};
}

export function ownedPapers(expert:ExpertCategory,papers:Paper[]) {
  return papers.filter(paper=>paper.status!=='pending'&&expert.paperIds.includes(paper.id)&&!expert.reviewIds?.includes(paper.id)&&!expert.excludedIds?.includes(paper.id));
}
export function knowledgeNotes(expert:ExpertCategory,papers:Paper[]) {
  return ownedPapers(expert,papers).flatMap(paper=>{
    const note=paperKnowledge[paper.id];if(!note)return [];
    const claims=getAnswerClaims(paper.id),measurements=getClaimMeasurements(claims);
    return [{paper,...note,claims,measurements,summary:claims.length?claims.map(claim=>kindLabels[claim.kind]+': '+claim.summary).join(' '):note.summary||'구조화된 근거를 기다리고 있습니다.'}];
  });
}
export function facetLabels(expert:ExpertCategory) {
  const limited=restrictedFacets(expert.facets);
  return limited.length?limited.flatMap(facet=>expert.facets![facet.key]!.length?expert.facets![facet.key]!:[''+facet.label+': 선택 없음']):['전체 분류'];
}
