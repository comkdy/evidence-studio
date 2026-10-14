import { evidenceData, getAnswerClaims, getClaimMeasurements, kindLabels } from './evidence.ts';
import { normalizeSearchText } from './terminology.ts';
import type { Answer, AnswerEvidenceSnapshot, ClaimMatch, Paper } from './types';

// Small deterministic fixture rules for the UI demonstration, not a retrieval engine.
// Topic matching reads the canonical claims; it does not author another paper summary.
const topics = [
  { label:'Retention', pattern:/retention/ },
  { label:'Refresh', pattern:/refresh/ },
  { label:'Endurance', pattern:/endurance|p\/e|program\/erase/ },
  { label:'Charge trapping', pattern:/charge trapping|전하.*트랩/ },
  { label:'Degradation', pattern:/degradation/ },
  { label:'Thermal', pattern:/thermal|temperature|온도|발열/ },
  { label:'Bandwidth', pattern:/bandwidth/ },
  { label:'Interconnect', pattern:/interconnect|tsv|packaging/ },
  { label:'Latency', pattern:/latency/ },
  { label:'ECC', pattern:/\becc\b|error correction/ },
  { label:'Energy / Power', pattern:/energy|power|전력/ },
];

export function selectQuestionClaims(question:string, papers:Paper[]):ClaimMatch[] {
  const text=normalizeSearchText(question);
  const requested=topics.filter(topic=>topic.pattern.test(text));
  const interpretation=/해석|메커니즘|mechanism|interpretation|왜|원인|가설|hypothes/.test(text);
  const results=/수치|측정값|얼마|몇|결과|비교|reported value|compare/.test(text);
  const broad=/정리|개요|주장|근거|동향|최근|이슈|overview|summary|reliability|비교|compare/.test(text);
  const platformOnly=/^(dram|nand|hbm|sram|mram|rram|pcm|ecc)$/.test(text);
  // Only exact temperature / voltage conditions are demonstrated here, with OR within a field.
  const temperatures=[...text.matchAll(/(-?\d+(?:\.\d+)?)\s*(?:°\s*c|℃|도)/g)].map(match=>Number(match[1]));
  const voltages=[...text.matchAll(/(\d+(?:\.\d+)?)\s*v\b/g)].map(match=>Number(match[1]));
  const conditionGroups=[{values:temperatures,unit:/°\s*c|℃/i,label:'°C'},{values:voltages,unit:/\bv\b/i,label:'V'}].filter(group=>group.values.length);
  if(!requested.length&&!broad&&!platformOnly&&!interpretation&&!conditionGroups.length)return [];

  const candidates=papers.filter(paper=>paper.status!=='pending'&&(!/\becc\b|error correction/.test(text)||/\becc\b|error correction/.test(normalizeSearchText(`${paper.title} ${paper.topic} ${paper.abstract}`))));
  return candidates.flatMap(paper=>getAnswerClaims(paper.id)).flatMap(claim=>{
    // A missing-condition mention in a raw quotation is not a measured outcome.
    const body=normalizeSearchText(claim.kind==='result'?`${claim.summary} ${getClaimMeasurements([claim]).map(item=>item.label).join(' ')}`:`${claim.summary} ${claim.rawText}`);
    const matched=requested.filter(topic=>topic.pattern.test(body));
    if(requested.length&&!matched.length)return [];
    if(interpretation&&!results&&claim.kind!=='interpretation'&&claim.kind!=='hypothesis')return [];
    if(results&&!interpretation&&claim.kind!=='result')return [];
    const measurements=getClaimMeasurements([claim]).filter(item=>conditionGroups.every(group=>item.conditions.some(condition=>{
      const raw=condition.rawValue?.normalize('NFKC')||'';
      return group.unit.test(raw)&&group.values.includes(Number(raw.match(/-?\d+(?:\.\d+)?/)?.[0]));
    })));
    if(conditionGroups.length&&!measurements.length)return [];
    if(/수치|측정값|얼마|몇/.test(text)&&!interpretation&&!measurements.length)return [];
    const reason=[matched.length?`${matched.map(topic=>topic.label).join(' · ')} 관련 주장`:'요청한 분야의 주장',...conditionGroups.map(group=>`${group.values.join(' / ')} ${group.label} 조건 일치`),kindLabels[claim.kind]].join(' · ');
    return [{claimId:claim.id,reason,measurementIds:measurements.map(item=>item.id),score:matched.length*10+(claim.kind==='result'?1:0)}];
  }).sort((a,b)=>b.score-a.score).slice(0,6).map(({claimId,reason,measurementIds})=>({claimId,reason,measurementIds}));
}

export function captureAnswerEvidence(matches:ClaimMatch[], capturedAt=new Date().toISOString()):AnswerEvidenceSnapshot {
  const unique=[...new Map(matches.map(match=>[match.claimId,match])).values()];
  const claims=unique.flatMap(match=>evidenceData.claims.find(claim=>claim.id===match.claimId&&claim.validation==='passed'&&claim.attribution==='authors')||[]);
  const measurements=getClaimMeasurements(claims).filter(item=>unique.some(match=>match.measurementIds.includes(item.id)&&claims.some(claim=>claim.id===match.claimId&&claim.measurementIds.includes(item.id)&&claim.paperId===item.paperId)));
  const spanIds=new Set([...claims.flatMap(claim=>claim.evidence.map(link=>link.spanId)),...measurements.flatMap(item=>[...item.evidenceIds,...item.conditions.flatMap(condition=>condition.evidenceIds)])]);
  const ids=new Set(claims.map(claim=>claim.id));
  return structuredClone({capturedAt,selectionVersion:'fixture-rules-v1',matches:unique.filter(match=>ids.has(match.claimId)).map(match=>({...match,measurementIds:match.measurementIds.filter(id=>measurements.some(item=>item.id===id&&claims.find(claim=>claim.id===match.claimId)?.measurementIds.includes(id)))})),claims,measurements,spans:evidenceData.spans.filter(span=>spanIds.has(span.id)),relations:evidenceData.relations.filter(relation=>ids.has(relation.from)&&ids.has(relation.to))});
}

export function selectedStatement(answer:Answer,paperId:string) {
  const claim=answer.evidence?.claims.find(item=>item.paperId===paperId);
  return answer.evidence?.spans.find(span=>span.id===claim?.evidence.find(link=>link.role==='statement')?.spanId);
}

export function comparisonReason(answer:Answer) {
  return answer.evidence?.relations[0]?.reason || (new Set(answer.evidence?.measurements.map(item=>item.label)).size>1?'측정 지표가 서로 다릅니다. 각 수치를 동일한 성능의 우열로 비교할 수 없습니다.':'비교에 필요한 조건과 대상이 충분히 일치하는지 확인해야 합니다. 이 예시에서는 우열을 판단하지 않습니다.');
}

export function answerSynthesis(answer:Answer) {
  return answer.mode==='claims'?'논문에 적힌 가능성은 확정된 메커니즘과 다릅니다. “may”의 강도를 유지하고, 주장에 직접 연결된 실험이 있는지 함께 확인해야 합니다.':answer.mode==='limits'?'현재 근거의 적용 범위를 벗어나는 조건에서는 결론을 유보합니다. 미기재 조건과 검증되지 않은 해석을 다음 확인 항목으로 남깁니다.':'각 결과는 해당 시료와 실험 조건 안에서 읽어야 합니다. 서로 다른 조건의 수치를 합치거나, 저자의 가능성 제안을 검증된 결론으로 넓히지 않습니다.';
}

export function answerPlainText(answer:Answer,papers:Paper[]) {
  const snapshot=answer.evidence;
  const heading=`화면 확인용 가상 답변 · 실제 연구 결과 아님\n질문: ${answer.question}\n참여 전문가: ${(answer.experts||[]).join(', ')}`;
  if(!snapshot?.claims.length)return `${heading}\n질문에 사용할 근거가 없습니다.`;
  return `${heading}\n\n`+snapshot.claims.map((claim,index)=>{
    const paper=papers.find(item=>item.id===claim.paperId);
    const numbers=snapshot.measurements.filter(item=>item.paperId===claim.paperId&&snapshot.matches.find(match=>match.claimId===claim.id)?.measurementIds.includes(item.id));
    const sourceIds=new Set([...claim.evidence.map(link=>link.spanId),...numbers.flatMap(item=>[...item.evidenceIds,...item.conditions.flatMap(condition=>condition.evidenceIds)])]);
    const sources=snapshot.spans.filter(span=>sourceIds.has(span.id));
    return `[${index+1}] ${kindLabels[claim.kind]}: ${claim.summary}\n선택 이유: ${snapshot.matches.find(match=>match.claimId===claim.id)?.reason}\n적용 범위: ${claim.scope}\n`+
      (numbers.length?numbers.map(item=>`${item.label}: ${item.rawValue} · ${item.sample}; ${item.conditions.map(condition=>`${condition.name} ${condition.rawValue??'미기재'}`).join(', ')}`).join('\n'):'이 주장에 직접 연결된 수치 없음')+
      (!claim.hasDirectSupport?'\n이 해석을 직접 검증한 실험 근거는 연결되어 있지 않습니다.':'')+
      `\n출처: ${paper?.title||claim.paperId}\n`+[...new Map(sources.map(span=>[span.id,span])).values()].map(span=>`p. ${span.page} · ${span.section} · ${span.locator}\n${span.quote}`).join('\n');
  }).join('\n\n')+(answer.mode==='compare'?`\n\n직접 비교 보류: ${comparisonReason(answer)}`:'')+`\n\n종합 해석 · 답변 예시: ${answerSynthesis(answer)}\n위 문단은 답변의 종합 해석이며, 논문 저자의 원문 주장이 아닙니다.`;
}
