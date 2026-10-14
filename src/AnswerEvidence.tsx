import { ArrowUpRight, Layers3, ShieldCheck } from 'lucide-react';
import { kindLabels } from './evidence';
import { answerSynthesis, comparisonReason } from './answerSelection';
import { SourceQuote } from './PaperEvidence';
import type { Answer, Paper } from './types';
import type { PaperTab } from './PaperEvidence';

export function AnswerEvidence({answer,papers,onPaper}:{answer:Answer;papers:Paper[];onPaper:(paper:Paper,tab?:PaperTab)=>void}) {
  const snapshot=answer.evidence;
  if(!snapshot?.claims.length)return <div className="knowledge-empty">이 질문에 사용할 근거가 없습니다. 질문이나 전문가를 다시 선택해 주세요.</div>;
  const measures=snapshot.measurements;
  return <div className="answer-body structured-answer">
    <h2>질문에 맞는 주장과 연결된 근거</h2>
    <p>선택한 주장 {snapshot.claims.length}개와 그에 연결된 수치 {measures.length}개로 정리했습니다. 저자의 해석은 실험 결과와 구분합니다.</p>
    {snapshot.claims.map((claim,index)=>{
      const paper=papers.find(item=>item.id===claim.paperId);
      const match=snapshot.matches.find(item=>item.claimId===claim.id);
      const numbers=measures.filter(item=>item.paperId===claim.paperId&&match?.measurementIds.includes(item.id));
      const span=snapshot.spans.find(item=>item.id===claim.evidence.find(link=>link.role==='statement')?.spanId);
      const sources=[...new Map(claim.evidence.flatMap(link=>snapshot.spans.find(item=>item.id===link.spanId)||[]).map(item=>[item.id,item])).values()];
      const contributors=answer.contributions?.filter(expert=>expert.claimMatches.some(item=>item.claimId===claim.id));
      return <section className="answer-finding selected-finding" key={claim.id}>
        <div className="finding-heading"><span className="point-number">{String(index+1).padStart(2,'0')}</span><span className={'claim-kind '+claim.kind}>{kindLabels[claim.kind]}</span><span className="finding-experts">{contributors?.map(expert=>expert.name).join(' · ')}</span></div>
        <h3>{claim.summary}</h3>
        <p className="selection-reason"><CheckReason/>{match?.reason}</p>
        <p className="finding-boundary"><strong>적용 범위</strong>{claim.scope}</p>
        {!claim.hasDirectSupport&&<p className="support-note">이 해석을 직접 검증한 실험 근거는 연결되어 있지 않습니다. 가능성을 확정된 메커니즘으로 읽지 않습니다.</p>}
        {numbers.length?<div className="linked-values">{numbers.map(item=><div key={item.id}><span>{item.label}</span><strong>{item.rawValue}</strong><small>{item.sample}</small></div>)}</div>:<p className="no-linked-value">이 주장에 직접 연결된 수치가 없습니다.</p>}
        <details className="selected-source"><summary>원문과 연결 수치·조건 확인 {span&&<span>p. {span.page} · {span.section}</span>}</summary>
          {sources.map(source=><SourceQuote key={source.id} span={source}/>)}
          {numbers.map(item=><section className="selected-conditions" key={item.id}><h4>{item.label} · {item.rawValue}</h4><dl className="condition-grid">{item.conditions.map(condition=><div key={condition.name}><dt>{condition.name}</dt><dd className={condition.rawValue===null?'unknown':''}>{condition.rawValue??'원문 미기재'}</dd></div>)}</dl>
            {[...new Set([...item.evidenceIds,...item.conditions.flatMap(condition=>condition.evidenceIds)])].filter(id=>!sources.some(source=>source.id===id)).map(id=><SourceQuote key={id} span={snapshot.spans.find(source=>source.id===id)}/>)}
          </section>)}
          {paper&&<button className="evidence-link" onClick={()=>onPaper(paper,'claims')}>논문 전체의 주장 보기<ArrowUpRight size={13}/></button>}
        </details>
        <div className="selected-paper">{paper?.title}{paper&&<button aria-label={'논문 '+paper.title+' 열기'} onClick={()=>onPaper(paper,'claims')}><ArrowUpRight size={14}/></button>}</div>
      </section>;
    })}
    {answer.mode==='compare'&&<section className="answer-comparison"><div className="comparison-heading"><Layers3 size={17}/><h3>선택된 수치의 조건 비교</h3></div>
      {!!measures.length&&<div className="comparison-scroll" role="region" aria-label="선택된 수치와 실험 조건" tabIndex={0}><table><thead><tr><th>시료 · 지표</th><th>원문 수치</th><th>측정 조건</th></tr></thead><tbody>{measures.map(item=><tr key={item.id}><td><strong>{item.sample}</strong><span>{item.label}</span></td><td>{item.rawValue}</td><td>{item.conditions.map(condition=><span key={condition.name} className={condition.rawValue===null?'unknown':''}>{condition.name} {condition.rawValue??'미기재'}</span>)}</td></tr>)}</tbody></table></div>}
      <div className="comparison-conclusion"><span className="tag amber">직접 비교 보류</span><p>{comparisonReason(answer)}</p></div>
    </section>}
    <section className="answer-synthesis"><span>종합 해석 · 답변 예시</span><p>{answerSynthesis(answer)}</p><small>위 문단은 답변의 종합 해석이며, 논문 저자의 원문 주장이 아닙니다.</small></section>
    <div className="answer-caution"><ShieldCheck size={17}/><div><strong>가상 문헌으로 구성한 답변</strong><p>모든 논문·주장·수치는 화면 확인용입니다. 실제 연구 판단에 사용할 수 없습니다.</p></div></div>
  </div>;
}

function CheckReason() { return <span aria-hidden="true" className="reason-dot"/>; }
