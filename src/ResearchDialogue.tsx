import { useEffect, useRef } from 'react';
import { ArrowUpRight, BookOpen, Copy, Check, LoaderCircle, MessageSquare, Lock, Share2 } from 'lucide-react';
import type { ConversationTurn } from './pilotData';
import type { ProgressEvent } from './researchConversation';
import { ResearchAnswerView } from './ResearchAnswerView';
import { ClaimCard } from './PilotViews';
import { answerCopyText } from './researchAnswer';
import { scientificNotation } from './scientificNotation';
import { AnswerFeedback, requestPhases, requestStates } from './KnowledgeProgress';
import './ResearchConversation.css';

export function ResearchDialogue({turns,progress,busy,onPaper,onRetry,onNotice,onShare,onChanged}:{turns:ConversationTurn[];progress:ProgressEvent[];busy:boolean;onPaper:(id:string)=>void;onRetry:(question:string)=>void;onNotice:(text:string)=>void;onShare:(turn:ConversationTurn)=>void;onChanged:()=>void}){
 const pending=useRef<HTMLDivElement>(null);
 useEffect(()=>{if(busy)pending.current?.scrollIntoView({behavior:'smooth',block:'nearest'});},[busy]);
 async function copy(turn:ConversationTurn){
  if(!turn.result)return;
  const result=turn.result;
  const evidence=[...result.claims.flatMap(c=>[c.summary,c.scope,...c.measurements.flatMap(m=>[m.label+': '+m.raw_value,...m.conditions.map(v=>v.name+': '+(v.raw_value??'미기재'))]),...c.sources.map(s=>'p. '+s.page_number+' '+s.quote)]),...result.hits.flatMap(h=>[h.title+' · p. '+h.page_number,h.text])].map(scientificNotation);
  try{await navigator.clipboard.writeText(answerCopyText(turn.question,turn.expertNames,result,evidence));onNotice('답변과 근거를 복사했습니다.');}catch{onNotice('복사할 수 없습니다. 답변을 선택해 복사해 주세요.');}
 }
 return <div className="research-conversation" aria-label="연구 대화">
  {!turns.length&&<div className="research-welcome"><MessageSquare size={24}/><h2>한 질문에서 끝나지 않는 연구 대화</h2><p>답변을 읽고 적용 조건을 묻거나, 다른 논문의 결과와 비교해 보세요.</p><span>질문과 답변이 이곳에 차례로 쌓입니다.</span></div>}
  {turns.map((turn,index)=><section className="research-turn" key={turn.id} aria-label={'대화 '+(index+1)}>
   <div className="research-question"><span>{turn.request?.is_owner===false?'팀 공유 질문':'나'}{turn.focusTitle?' · '+turn.focusTitle:''}</span><p>{turn.question}</p></div>
   <div className="research-response">
    {!!turn.expertNames.length&&<div className="pilot-contributors"><BookOpen size={16}/><span>참여 전문가<strong>{turn.expertNames.join(' · ')}</strong></span></div>}
    {turn.reportChange&&<div className="report-change-result"><Check size={17}/><div><strong>{turn.reportChange.sectionTitle} 수정 완료 · v{turn.reportChange.revision}</strong><p>다른 구간은 유지했습니다. 보고서의 출처와 변경 이력에서 확인할 수 있습니다.</p><small>{turn.reportChange.model} · {(turn.reportChange.elapsedMs/1000).toFixed(1)}초</small></div></div>}
    {turn.result&&<>
     <ResearchAnswerView result={turn.result}/>
     <details className="research-evidence" open={turn.status==='pending'||undefined}><summary>근거 보기 · 주장 {turn.result.total_claims}개 · 원문 {turn.result.hits.length}개</summary>
      {turn.result.claims.map(c=><ClaimCard key={c.id} claim={c} onPaper={onPaper}/>)}
      {turn.result.hits.map(h=><article className="pilot-hit" key={h.id}><button onClick={()=>onPaper(h.paper_id)}>{h.title}<ArrowUpRight size={14}/></button><p>{h.text}</p><a href={'/api/pilot/documents/'+h.document_id+'/pdf#page='+h.page_number} target="_blank" rel="noreferrer">p. {h.page_number}{h.page_end&&h.page_end!==h.page_number?'–'+h.page_end:''} · 원문 PDF</a>{h.parent_context&&h.parent_context.text!==h.text&&<details className="pilot-hit-context"><summary>앞뒤 문맥 함께 보기</summary><p>{h.parent_context.text}</p></details>}</article>)}
      {!turn.result.claims.length&&!turn.result.hits.length&&<p className="pilot-muted">담당 범위에서 사용할 근거를 찾지 못했습니다.</p>}
     </details>
     <button className="research-copy" onClick={()=>copy(turn)}><Copy size={14}/>답변과 근거 복사</button>
    </>}
    {turn.status==='pending'&&<div className="research-progress" ref={pending}>
     <div className="research-progress-top"><div className="research-progress-heading" role="status" aria-live="polite"><LoaderCircle size={17}/><span>{turn.request?(requestPhases[turn.request.phase]??requestStates[turn.request.status]):progress.at(-1)?.message??'질문을 등록하고 원천 근거를 찾고 있습니다.'}</span></div></div>
     {turn.durable?<p className="pilot-muted">다른 화면으로 이동해도 계속 준비합니다. 내 페이지에서 다시 열 수 있습니다.</p>:<ol aria-label="답변 처리 단계">{progress.slice(-6).map((p,i,a)=><li key={i} className={i===a.length-1?'active':''}>{i===a.length-1?<LoaderCircle size={13}/>:<Check size={13}/>}<span>{p.message}</span></li>)}</ol>}
    </div>}
    {(turn.status==='stopped'||turn.status==='error')&&<div className={'research-state '+turn.status} role="status"><p>{turn.message??(turn.status==='stopped'?'답변을 중지했습니다. 이어서 질문할 수 있습니다.':'답변을 준비하지 못했습니다.')}</p><button className="pilot-inline-link" disabled={busy} onClick={()=>onRetry(turn.question)}>질문 다시 입력</button></div>}
    {turn.request&&<div className="research-request-actions"><span>{turn.status==='error'&&turn.request.status!=='failed'?'열람 상태 확인':requestStates[turn.request.status]} · {turn.request.visibility==='team'?'팀에 공유됨':'나만 보기'}{turn.request.cache_hit?' · 검토한 답변 재사용':''}</span>{turn.request.is_owner!==false&&<button className="secondary" disabled={turn.request.visibility==='private'&&turn.request.learning_eligible===false} onClick={()=>onShare(turn)}>{turn.request.visibility==='team'?<Lock size={13}/>:<Share2 size={13}/>} {turn.request.visibility==='team'?'나만 보기로 변경':'이 질문을 팀에 공유'}</button>}</div>}
    {turn.request?.learning_eligible===false&&<p className="pilot-muted">개인 대화·보고서 맥락을 포함하는 질문은 팀에 공유하거나 팀의 준비 질문으로 사용할 수 없습니다.</p>}
    {turn.request?.is_owner===false&&<p className="pilot-muted">이 질문의 개인 대화 기록은 참조할 수 없습니다. 이어지는 질문은 원문 근거에서 새로 조사합니다.</p>}
    {turn.request&&turn.status==='completed'&&<AnswerFeedback request={turn.request} onChanged={onChanged}/>}
   </div>
  </section>)}
 </div>;
}
