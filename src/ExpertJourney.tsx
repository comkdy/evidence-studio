import { ArrowRight, BookOpen, Check, Sparkles } from 'lucide-react';
import { knowledgeNotes, ownedPapers, paperKnowledge } from './knowledge';
import { captureAnswerEvidence } from './answerSelection';
import { kindLabels } from './evidence';
import { buildContributions, recommendationReason, routeQuestion } from './experts';
import type { Answer, ExpertCategory, Paper } from './types';

export function ExpertRecommendation({question,categories,papers,selectedIds,onSelection,onConfirm,onCancel}:{question:string;categories:ExpertCategory[];papers:Paper[];selectedIds:string[];onSelection:(ids:string[])=>void;onConfirm:()=>void;onCancel:()=>void}) {
  const recommended=routeQuestion(question,categories);
  const available=categories.filter(expert=>expert.enabled);
  const primary=available.filter(expert=>recommended.some(item=>item.id===expert.id)||selectedIds.includes(expert.id));
  const other=available.filter(expert=>!primary.some(item=>item.id===expert.id));
  const choices=available.filter(expert=>selectedIds.includes(expert.id));
  const contributions=buildContributions(question,papers,choices);
  const selection=captureAnswerEvidence(contributions.flatMap(item=>item.claimMatches));
  const canAnswer=selection.claims.length>0;
  const card=(expert:ExpertCategory)=>{const count=ownedPapers(expert,papers).length;return <label className={`recommendation-card ${selectedIds.includes(expert.id)?'selected':''}`} key={expert.id}><input type="checkbox" checked={selectedIds.includes(expert.id)} onChange={event=>onSelection(event.target.checked?[...selectedIds,expert.id]:selectedIds.filter(id=>id!==expert.id))}/><span><strong>{expert.name}{recommended.some(item=>item.id===expert.id)&&<small>추천</small>}</strong><p>{recommendationReason(question,expert)}</p><span className="recommendation-scope">{expert.scope}</span><span className="recommendation-count"><BookOpen size={13}/>{count?`담당 논문 ${count}편 · 확인된 주장 ${knowledgeNotes(expert,papers).reduce((count,note)=>count+note.claims.length,0)}개`:'현재 질문 범위에 연결된 담당 논문이 없습니다'}</span></span></label>;};
  return <section className="expert-recommendation"><div className="journey-label"><span>질문</span><ArrowRight size={13}/><strong>전문가 확인</strong><ArrowRight size={13}/><span>근거와 답변</span></div><h1>{question}</h1><div className="recommendation-heading"><Sparkles size={19}/><div><h2>이 질문에 함께할 전문가</h2><p>담당 분야를 확인하고, 함께 답할 전문가를 바꿀 수 있어요.</p></div></div>
    {!recommended.length&&<div className="knowledge-empty"><strong>적합한 전문가를 자동으로 찾지 못했습니다.</strong><p>아래에서 전문가 개요를 확인해 직접 선택하세요. 다른 분야의 자료로 임의 답변하지 않습니다.</p></div>}
    {!available.length&&<p className="scope-warning">사용 가능한 전문가가 없습니다. 관리자에게 전문가 설정을 요청해 주세요.</p>}
    <div className="recommendation-list">{primary.map(card)}</div>
    {!!other.length&&<details className="other-experts" open={!primary.length}><summary>다른 전문가 함께 보기 · {other.length}명</summary><div className="recommendation-list">{other.map(card)}</div></details>}
    {!!choices.length&&!canAnswer&&<p className="scope-warning" role="status">질문의 주제·조건에 맞는 확인된 주장을 찾지 못했습니다. 질문이나 전문가를 바꿔 주세요. 논문이 있어도 연결된 주장이 없으면 답변을 보류합니다.</p>}
    {canAnswer&&contributions.some(item=>!item.paperIds.length)&&<p className="scope-warning">담당 근거가 없는 전문가는 답변을 보류하고, 근거가 있는 전문가의 답변만 구성합니다.</p>}
    {canAnswer&&<section className="question-evidence-preview" aria-label="질문에 맞춘 근거 미리보기"><header><div><h3>이 질문에 사용할 근거</h3><p>전문가의 담당 논문 중 질문의 주제와 조건에 맞는 주장을 골랐습니다.</p></div><span>주장 {selection.claims.length} · 수치 {selection.measurements.length}</span></header><ol>{selection.claims.map(claim=><li key={claim.id}><span className={"claim-kind "+claim.kind}>{kindLabels[claim.kind]}</span><div><p>{claim.summary}</p><small>{selection.matches.find(match=>match.claimId===claim.id)?.reason}</small></div></li>)}</ol><footer>수치는 선택한 주장과 연결된 것만 사용합니다.</footer></section>}
    <div className="recommendation-footer"><button className="quiet-button" onClick={onCancel}>질문 수정</button><button className="primary" disabled={!canAnswer} onClick={onConfirm}>선택한 전문가로 답변 보기<ArrowRight size={15}/></button></div><p className="section-caption">가상 문헌의 주제·일부 조건을 맞추는 화면 예시입니다. 실제 검색·LLM을 호출하지 않습니다.</p>
  </section>;
}

export function ExpertContributions({answer,papers,onPaper}:{answer:Answer;papers:Paper[];onPaper:(paper:Paper)=>void}) {
  if(!answer.contributions)return null;
  const topical=/최근|이슈|동향|주목/.test(answer.question);
  return <section className="expert-contributions" aria-label="전문가별 답변 근거"><h2>전문가별로 살펴본 지식</h2>{answer.contributions.map(expert=><article key={expert.id}><div className="contribution-title"><span><Check size={14}/>{expert.name}</span><small>{expert.paperIds.length?`논문 ${expert.paperIds.length}편 · 주장 ${expert.claimMatches.length} · 수치 ${expert.measurementIds.length}`:'근거 부족 · 답변 보류'}</small></div><p>{expert.reason}</p>{expert.paperIds.length?<><div className="contribution-notes">{expert.knowledgeTitles.map(title=><span key={title}>{title}</span>)}</div>{topical&&expert.paperIds.map(id=>paperKnowledge[id]?<p className="contribution-insight" key={id}>{paperKnowledge[id].issue}</p>:null)}<div className="contribution-sources">{expert.paperIds.flatMap(id=>{const paper=papers.find(item=>item.id===id);return paper?[<button key={id} onClick={()=>onPaper(paper)}><BookOpen size={12}/>{paper.title}</button>]:[];})}</div></>:<p>담당 논문 안에서 질문의 주제·조건에 맞는 확인된 주장을 찾지 못했습니다.</p>}<footer>담당 지식 기준 {expert.asOf} · 가상 자료</footer></article>)}</section>;
}
