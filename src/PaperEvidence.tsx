import { useState } from 'react';
import { ArrowRight, ArrowUpRight, BookOpen, FileText, Quote } from 'lucide-react';
import { evidenceData, getClaims, getMeasurements, getSpan, kindLabels } from './evidence';
import type { EvidenceSpan, Paper } from './types';

export type PaperTab = 'overview' | 'claims' | 'measurements';

export function SourceQuote({span}:{span:EvidenceSpan|undefined}) {
  if(!span)return null;
  return <blockquote className="source-quote"><p lang="en">“{span.quote}”</p><footer><Quote size={12}/>가상 원문 · p. {span.page} · {span.section} · {span.locator}</footer></blockquote>;
}

export function PaperEvidence({paper,papers,initialTab,onPaper,onAsk}:{paper:Paper;papers:Paper[];initialTab:PaperTab;onPaper:(paper:Paper)=>void;onAsk:()=>void}) {
  const [tab,setTab]=useState<PaperTab>(initialTab);
  const claims=getClaims(paper.id);
  const measurements=getMeasurements(paper.id);
  const relations=evidenceData.relations.filter(relation=>claims.some(claim=>claim.id===relation.from||claim.id===relation.to));
  return <>
    <span className="tag blue">{paper.topic}</span><h2 id="dialog-title">{paper.title}</h2>
    <p className="muted">{paper.authors} · {paper.year} · {paper.pages} pages</p>
    <div className="paper-tabs" role="group" aria-label="논문 상세 보기">
      {([['overview','개요',null],['claims','주장·근거',claims.length],['measurements','수치·조건',measurements.length]] as const).map(([id,label,count])=><button key={id} className={tab===id?'active':''} aria-pressed={tab===id} onClick={()=>setTab(id)}>{label}{count!==null&&<span>{count}</span>}</button>)}
    </div>
    <p className="fiction-note">논문·주장·수치는 모두 화면 확인용 가상 데이터입니다.</p>
    {tab==='overview'&&<div className="paper-overview"><h3>초록</h3><p className="abstract">{paper.abstract}</p><div className="paper-evidence-links"><button onClick={()=>setTab('claims')}><Quote size={20}/><strong>주장 {claims.length}개</strong><span>저자의 결과와 해석 확인<ArrowRight size={14}/></span></button><button onClick={()=>setTab('measurements')}><BookOpen size={20}/><strong>측정값 {measurements.length}개</strong><span>원문 수치와 조건 확인<ArrowRight size={14}/></span></button></div><div className="detail-file"><FileText size={19}/><span>{paper.filename}<small>가상 파일입니다. 실제 PDF는 연결되지 않았습니다.</small></span></div></div>}
    {tab==='claims'&&<div className="claim-list">
      {!claims.length&&<div className="evidence-placeholder"><Quote size={24}/><h3>아직 정리된 주장이 없습니다</h3><p>확인된 원문과 연결된 주장이 이곳에 표시됩니다.</p></div>}
      {claims.map(claim=><article className={`claim-card ${claim.kind}`} key={claim.id}>
        <div className="claim-card-top"><span className={`claim-kind ${claim.kind}`}>{kindLabels[claim.kind]}</span><span>{claim.attribution==='authors'?'이 논문의 저자':'다른 연구를 인용'} · {claim.certainty}</span></div>
        <h3>{claim.summary}</h3><p className="claim-scope"><strong>적용 범위</strong>{claim.scope}</p>
        <SourceQuote span={getSpan(claim.evidence.find(link=>link.role==='statement')?.spanId||'')}/>
        {claim.hasDirectSupport?<button className="evidence-link" onClick={()=>setTab('measurements')}>연결된 실험 수치 {claim.measurementIds.length}개<ArrowRight size={14}/></button>:<p className="support-note">이 해석을 직접 검증한 실험 근거는 연결되어 있지 않습니다.</p>}
        <div className={`claim-review ${claim.validation==='needs-review'?'pending':''}`}>
          <span>{claim.validation==='passed'?'원문 대조 통과':'원문 대조 필요 · 답변 인용 제외'}</span><span>사람 검토 {claim.review==='reviewed'?'완료':'전'} · 예시 상태</span>
        </div>
      </article>)}
      {!!relations.length&&<section className="related-evidence"><h3>다른 논문의 주장과 함께 보기</h3>{relations.map(relation=>{
        const otherId=claims.some(claim=>claim.id===relation.from)?relation.to:relation.from;
        const other=evidenceData.claims.find(claim=>claim.id===otherId);
        const otherPaper=papers.find(item=>item.id===other?.paperId);
        return <div key={relation.id}><span className="tag amber">{relation.comparisonStatus==='different-conditions'?'조건 차이 · 직접 비교 보류':'검토할 관계'}</span><p>{relation.reason}</p>{otherPaper&&<button onClick={()=>onPaper(otherPaper)}>{otherPaper.title}<ArrowUpRight size={14}/></button>}</div>;
      })}</section>}
      {!!claims.length&&<p className="review-explanation">‘원문 대조’는 진술과 출처의 일치 여부를 뜻합니다. 주장 자체의 과학적 타당성을 보증하지는 않습니다.</p>}
    </div>}
    {tab==='measurements'&&<div className="measurement-list">
      {!measurements.length&&<div className="evidence-placeholder"><BookOpen size={24}/><h3>아직 연결된 측정값이 없습니다</h3><p>원문에 없는 수치나 조건은 채워 넣지 않습니다.</p></div>}
      {measurements.map(item=><article className="measurement-card" key={item.id}><div className="measurement-heading"><div><span>{item.label}</span><h3>{item.rawValue}</h3></div><span className="tag">{item.sample}</span></div>
        <p className="normalized-value">단위 환산 · {item.operator==='>='?'≥ ':''}{item.normalizedValue.toLocaleString('en-US',{maximumSignificantDigits:12})} {item.normalizedUnit}{item.operator==='>='&&<strong>최소값 · 정확한 값으로 해석하지 않음</strong>}</p>
        <h4>이 측정값의 실험 조건</h4><dl className="condition-grid">{item.conditions.map(condition=><div key={condition.name}><dt>{condition.name}</dt><dd className={condition.rawValue===null?'unknown':''}>{condition.rawValue??'원문 미기재'}</dd></div>)}</dl>
        {item.conditions.some(condition=>condition.rawValue===null)&&<p className="support-note">미기재 조건은 알 수 없는 값으로 남겨둡니다. 동일 조건의 결과인지 먼저 확인해야 합니다.</p>}
        <SourceQuote span={getSpan(item.evidenceIds[0])}/>
      </article>)}
    </div>}
    <div className="paper-detail-footer"><button className="primary full" onClick={onAsk}>이 논문으로 질문하기<ArrowRight size={15}/></button></div>
  </>;
}
