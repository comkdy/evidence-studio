import type { Answer, Claim, ExpertCategory, Paper } from './types';
import { ownedPapers, knowledgeNotes } from './knowledge.ts';
import { evidenceData, getClaimMeasurements } from './evidence.ts';
import { normalizeSearchText } from './terminology.ts';

// Authored UI fixtures and in-memory revisions only; no Wiki generation service.
export type WikiBlock = { id:string; kind:'knowledge'|'gap'|'faq'|'pending'; title:string; text:string; paperIds:string[]; claimIds:string[]; measurementIds:string[]; evidenceIds:string[] };
export type WikiRevision = { version:number; at:string; reason:string; paperIds:string[]; blocks:WikiBlock[]; addedPaperIds:string[]; removedPaperIds:string[] };
export type WikiPage = { id:string; expertId:string; topicId:string; title:string; revisions:WikiRevision[]; faqEnabled:boolean; lastCheckedAt?:string; reviewedCount?:number };
export const wikiTopics = [
  {id:'dram-retention',title:'DRAM · Retention과 reliability',paperIds:['p1','p6'],question:'Retention 결과를 비교할 때 어떤 조건을 함께 확인해야 하나요?',count:8,platform:'dram',keywords:['retention','refresh','reliability']},
  {id:'nand-reliability',title:'NAND · Endurance와 retention',paperIds:['p2','p8'],question:'Endurance 결과와 retention 특성을 함께 해석할 때 주의할 점은?',count:9,platform:'nand',keywords:['endurance','retention','reliability']},
  {id:'hbm-thermal',title:'HBM · Stack thermal conditions',paperIds:['p3','p7'],question:'HBM thermal 결과를 비교하려면 어떤 조건을 맞춰야 하나요?',count:12,platform:'hbm',keywords:['thermal','온도','packaging']},
  {id:'hbm-interconnect',title:'HBM · Interconnect와 bandwidth',paperIds:['p4'],question:'서로 다른 interconnect 구조의 bandwidth를 직접 비교해도 되나요?',count:5,platform:'hbm',keywords:['interconnect','bandwidth']},
  {id:'controller-ecc',title:'Controller · ECC 평가 조건',paperIds:['p5'],question:'ECC 성능을 비교할 때 빠뜨리기 쉬운 조건은 무엇인가요?',count:6,platform:'',keywords:['ecc','error correction']},
];

// Headlines describe what a reader can learn. Claim types remain source metadata.
// Text and numbers reuse the canonical evidence; these templates are not another extractor.
const knowledgeTitles:Record<string,string>={
  'dram-retention':'Retention time은 시료와 측정 온도를 함께 확인해야 비교할 수 있다',
  'nand-reliability':'보고된 NAND endurance는 정확한 수명이 아닌 최소값이다',
  'hbm-thermal':'HBM temperature는 측정 위치·power·ambient temperature 조건과 함께 읽어야 한다',
  'hbm-interconnect':'보고된 HBM bandwidth는 channel 수와 traffic 조건에 한정된다',
  'controller-ecc':'보고된 ECC latency는 single-bit error 시험에 해당한다',
};
const openQuestionTitles:Record<string,string>={
  'p1-interpretation':'Refresh interval을 늘려도 되는지는 현재 근거로 확정할 수 없다',
  'p2-interpretation':'Charge trapping이 degradation을 일으킨다는 설명은 직접 검증되지 않았다',
  'p3-interpretation':'Heat spreading 개선 효과는 대안 stack에서 확인되지 않았다',
  'p4-interpretation':'Interconnect의 energy per bit 개선 여부는 확인되지 않았다',
  'p6-interpretation':'Temperature와 cell design의 영향은 분리해서 검증되지 않았다',
};

function references(claims:Claim[]) {
  const measurements=getClaimMeasurements(claims);
  return {
    paperIds:[...new Set(claims.map(claim=>claim.paperId))],
    claimIds:claims.map(claim=>claim.id),
    measurementIds:measurements.map(item=>item.id),
    evidenceIds:[...new Set([...claims.flatMap(claim=>claim.evidence.map(link=>link.spanId)),...measurements.flatMap(item=>[...item.evidenceIds,...item.conditions.flatMap(condition=>condition.evidenceIds)])])],
  };
}

function blocksFor(topicId:string,ids:string[],faq:boolean,papers:Paper[]):WikiBlock[] {
  const notes=knowledgeNotes({id:'wiki-view',name:'',scope:'',keywords:'',enabled:true,paperIds:ids},papers);
  const claims=notes.flatMap(note=>note.claims);
  const results=claims.filter(claim=>claim.kind==='result');
  const blocks:WikiBlock[]=[];
  // A knowledge point stays small as a topic grows. Further evidence is available by page.
  // Groups do not cross the expert's admitted paper boundary.
  for(let offset=0;offset<results.length;offset+=3){
    const group=results.slice(offset,offset+3);
    const relation=evidenceData.relations.find(item=>group.some(claim=>claim.id===item.from)&&group.some(claim=>claim.id===item.to));
    const qualifiers=getClaimMeasurements(group).flatMap(item=>item.conditions.filter(condition=>condition.rawValue===null).map(condition=>condition.name));
    const context=relation?.reason||(qualifiers.length?[...new Set(qualifiers)].join(' · ')+' 조건은 원문에 미기재되어 있어 동일 조건인지 확인할 수 없다.':topicId==='nand-reliability'?'이 값으로 정확한 lifetime이나 retention time을 판단할 수는 없다.':'현재 자료에서 확인된 범위는 위 시료와 시험 조건이다.');
    blocks.push({id:'knowledge-'+topicId+'-'+offset,kind:'knowledge',title:knowledgeTitles[topicId]||'등록된 자료에서 확인되는 내용',text:group.map(claim=>claim.summary).join(' ')+' '+context,...references(group)});
  }
  for(const claim of claims.filter(item=>item.kind!=='result')){
    blocks.push({id:'question-'+claim.id,kind:'gap',title:openQuestionTitles[claim.id]||'이 설명의 검증 범위는 추가로 확인해야 한다',text:claim.summary,...references([claim])});
  }
  for(const claim of results){
    const missing=getClaimMeasurements([claim]).flatMap(item=>item.conditions.filter(condition=>condition.rawValue===null).map(condition=>condition.name));
    if(missing.length)blocks.push({id:'conditions-'+claim.id,kind:'gap',title:[...new Set(missing)].join(' · ')+' 조건을 알 수 없어 비교에 한계가 있다',text:'등록된 원문에는 해당 조건이 기재되어 있지 않다. 다른 자료와 동일한 시험 조건이었다고 가정하지 않는다.',...references([claim])});
  }
  for(const note of notes.filter(note=>!note.claims.length)){
    blocks.push({id:'pending-'+note.paper.id,kind:'pending',title:note.title+'는 근거 확인을 기다리고 있다',text:'담당 문헌에 추가되었지만 확인된 주장·수치가 아직 없다. 핵심 지식과 FAQ의 근거로 사용하지 않는다.',paperIds:[note.paper.id],claimIds:[],measurementIds:[],evidenceIds:[]});
  }
  const topic=wikiTopics.find(item=>item.id===topicId);
  const primary=blocks.find(block=>block.kind==='knowledge');
  if(faq&&topic&&primary)blocks.push({id:'faq-'+topicId,kind:'faq',title:topic.question,text:primary.text,paperIds:[...primary.paperIds],claimIds:[...primary.claimIds],measurementIds:[...primary.measurementIds],evidenceIds:[...primary.evidenceIds]});
  return blocks;
}

export function syncExpertWikis(previous:WikiPage[],experts:ExpertCategory[],papers:Paper[],at:string):WikiPage[] {
  const pages=experts.flatMap(expert=>{
    const allowed=ownedPapers(expert,papers).map(paper=>paper.id);
    return wikiTopics.flatMap(topic=>{
      const ids=topic.paperIds.filter(id=>allowed.includes(id));
      if(!ids.length)return [];
      const id=expert.id+':'+topic.id,old=previous.find(page=>page.id===id);
      const last=old?.revisions.at(-1);
      const blocks=blocksFor(topic.id,ids,old?.faqEnabled||false,papers);
      if(last&&last.paperIds.join('|')===ids.join('|')&&JSON.stringify(last.blocks)===JSON.stringify(blocks))return [old!];
      const added=ids.filter(id=>!last?.paperIds.includes(id));
      const removed=(last?.paperIds||[]).filter(id=>!ids.includes(id));
      const reason=!last?'초기 지식 구성':added.some(id=>['p7','p8'].includes(id))?'새 논문 반영':added.length||removed.length?'담당 논문 변경':'연결된 근거 교정';
      const revision:WikiRevision={version:(last?.version||0)+1,at,reason,paperIds:ids,blocks,addedPaperIds:added,removedPaperIds:removed};
      return [{id,expertId:expert.id,topicId:topic.id,title:topic.title,faqEnabled:old?.faqEnabled||false,lastCheckedAt:old?.lastCheckedAt,reviewedCount:old?.reviewedCount,revisions:[...(old?.revisions||[]),revision]}];
    });
  });
  return pages.length===previous.length&&pages.every((page,index)=>page===previous[index])?previous:pages;
}

export function sharedQuestionCount(page:WikiPage,history:Answer[]) {
  const topic=wikiTopics.find(item=>item.id===page.topicId)!;
  // Baseline counts are explicitly labeled fictional. Only visible team questions add to them.
  return topic.count+history.filter(item=>item.visibility==='team'&&!item.deleted).filter(item=>{
    const text=normalizeSearchText(item.question);
    return (!topic.platform||text.includes(topic.platform))&&topic.keywords.some(word=>text.includes(word));
  }).length;
}

export function reviewWikiQuestions(page:WikiPage,at:string,count:number,papers:Paper[]):WikiPage {
  const last=page.revisions.at(-1)!;
  if(page.faqEnabled||!last.blocks.some(block=>block.kind==='knowledge'&&block.claimIds.length))return {...page,lastCheckedAt:at,reviewedCount:count};
  const revision:WikiRevision={version:last.version+1,at,reason:'반복 질문에 따른 FAQ 보강',paperIds:[...last.paperIds],blocks:blocksFor(page.topicId,last.paperIds,true,papers),addedPaperIds:[],removedPaperIds:[]};
  return {...page,faqEnabled:true,lastCheckedAt:at,reviewedCount:count,revisions:[...page.revisions,revision]};
}

export function wikiChanges(revision:WikiRevision,previous?:WikiRevision) {
  const added=revision.blocks.filter(block=>!previous?.blocks.some(old=>old.id===block.id));
  const edited=revision.blocks.flatMap(block=>{const old=previous?.blocks.find(item=>item.id===block.id);return old&&JSON.stringify(old)!==JSON.stringify(block)?[{before:old,after:block}]:[];});
  const removed=(previous?.blocks||[]).filter(block=>!revision.blocks.some(item=>item.id===block.id));
  return {added,edited,removed};
}

export function wikiTime(value:string) {return new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value))+' KST';}
