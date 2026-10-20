import { useEffect, useState } from 'react';
import { ArrowUpRight, CheckCircle2, Clock3, MessageSquare, Send } from 'lucide-react';
import { api } from './identity';
import { get, time } from './pilotData';
import type { KnowledgeProgress as Progress, ResearchRequest, ResearchRequestSummary, RequestState } from './pilotData';
import './KnowledgeProgress.css';

const questionStates:Record<string,string>={queued:'근거 준비 대기',evidence_ready:'원천 근거 준비됨',needs_evidence:'추가 근거 필요',answer_validated:'답변 검토됨'};
const gapKinds:Record<string,string>={missing_sources:'자료 부족',extraction_gap:'원문 추출·분석',retrieval_gap:'검색 근거',condition_gap:'비교 조건',conflict_gap:'상충 가능성',incorrect:'정확성 검토',missing_evidence:'추가 근거 요청',unclear:'설명 확인'};
export const requestStates:Record<ResearchRequest['status'],string>={queued:'순서 대기',running:'답변 준비 중',completed:'완료',cancelled:'취소됨',failed:'확인 필요'};
export const requestPhases:Record<string,string>={queued:'모델 작업 순서를 기다리고 있습니다.',running:'질문에 사용할 근거를 준비하고 있습니다.',evidence:'논문 원문과 근거를 확인하고 있습니다.',plan:'질문의 조사 범위와 조건을 정리하고 있습니다.',plan_completed:'조사 계획에 따라 관련 자료를 찾고 있습니다.',refine:'빠진 근거와 비교 조건을 확인하고 있습니다.',refine_completed:'추가 검색 결과를 모으고 있습니다.',answer:'근거에 따라 종합 답변을 작성하고 있습니다.',answer_completed:'작성된 답변의 검토를 준비하고 있습니다.',review:'주장과 인용이 원천 근거에 맞는지 검토하고 있습니다.',review_completed:'검토 결과를 반영하고 있습니다.',publishing:'검토 결과와 인용을 저장하고 있습니다.',completed:'저장된 결과를 확인할 수 있습니다.',cancelled:'이 질문의 준비 작업을 중단했습니다.',failed:'작업을 완료하지 못했습니다. 아래 내용을 확인해 주세요.',prepare_evidence:'질문에 필요한 원천 근거를 준비하고 있습니다.',prepare_plan:'질문의 조사 범위를 정리하고 있습니다.',prepared_plan:'조사 계획에 따라 관련 자료를 찾고 있습니다.',prepare_refine:'추가로 필요한 근거를 확인하고 있습니다.',prepared_refine:'질문에 사용할 근거를 모으고 있습니다.'};

export function useKnowledgeProgress(revision:number,enabled=true){
 const [items,setItems]=useState<Progress[]>([]),[error,setError]=useState('');
 useEffect(()=>{if(!enabled)return;const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
  async function refresh(){try{if(!document.hidden){const data=await get<{experts:Progress[]}>('/knowledge/progress',controller.signal);setItems(data.experts);setError('');}}catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:'지식 준비 상태를 불러오지 못했습니다.');}finally{if(!controller.signal.aborted)timer=setTimeout(refresh,10000);}}
  void refresh();return()=>{controller.abort();clearTimeout(timer);};
 },[revision,enabled]);
 return {items,error};
}

export function KnowledgePreparation({expert,progress,onChanged,onAsk}:{expert:{id:string;name:string};progress?:Progress;onChanged:()=>void;onAsk:(question:string)=>void}){
 const [question,setQuestion]=useState(''),[busy,setBusy]=useState(false),[notice,setNotice]=useState('');
 const chunks=(progress?.coverage.papers||[]).reduce((sum,paper)=>({ready:sum.ready+paper.ready_chunks,total:sum.total+paper.total_chunks}),{ready:0,total:0});
 async function add(){if(!question.trim()||busy)return;setBusy(true);setNotice('');try{await api('/api/pilot/knowledge/questions',{method:'POST',body:JSON.stringify({expert_id:expert.id,question})});setQuestion('');setNotice('팀의 준비 질문에 등록했습니다. 작업 한도 안에서 순서대로 준비합니다.');onChanged();}catch(e){setNotice(e instanceof Error?e.message:'질문을 등록하지 못했습니다.');}finally{setBusy(false);}}
 return <section className="knowledge-preparation" aria-label={expert.name+' 지식 준비 상태'}>
  <header><div><h2>어디까지 준비되어 있나요?</h2><p>원천 지식의 범위와 다음에 확인할 질문을 함께 봅니다.</p></div><Clock3 size={20}/></header>
  {progress?<><div className="knowledge-metrics"><span><strong>{progress.coverage.ready}/{progress.coverage.total}</strong>논문 원천 준비</span><span><strong>{progress.coverage.partial}</strong>부분 준비</span><span><strong>{progress.question_readiness.evidence_ready}/{progress.question_readiness.total}</strong>대표 질문의 근거 준비</span><span><strong>{progress.gap_count??progress.gaps.length}</strong>열린 확인 과제</span></div>
   <div className="knowledge-source-coverage"><label htmlFor="source-chunk-progress">원문 분석 구간 <strong>{chunks.ready} / {chunks.total}</strong> 준비</label><progress id="source-chunk-progress" aria-label="원문 분석 구간 준비" max={Math.max(chunks.total,1)} value={chunks.ready}/><small>구간 전체는 현재 추출 가능한 원문 범위입니다. 이미지·표 등 추출되지 않은 내용은 별도 확인이 필요합니다.</small></div>
   <p className="knowledge-disclaimer">근거 준비는 과학적 정답 검증과 다릅니다. 논문 원문과 적용 조건을 함께 확인하세요.</p>
   <div className="knowledge-columns"><div><h3>준비 중인 대표 질문</h3>{progress.questions.slice(0,6).map(q=><button className="knowledge-question" key={q.id} onClick={()=>onAsk(q.question)}><span>{q.question}<small>{questionStates[q.state]||q.state}</small></span><ArrowUpRight size={14}/></button>)}{!progress.questions.length&&<p>공유된 질문이나 앞으로 확인할 질문을 등록하면 준비합니다.</p>}</div>
    <div><h3>추가로 확인할 근거</h3>{progress.gaps.slice(0,5).map(g=><div className="knowledge-gap" key={g.id}><strong>{gapKinds[g.kind]||'확인 과제'}</strong><p>{g.detail}</p>{g.next_action&&<small>다음 조치: {g.next_action}</small>}</div>)}{!progress.gaps.length&&<p>현재 등록된 확인 과제가 없습니다. 지식이 완전하다는 뜻은 아닙니다.</p>}
     <h3>최근 준비 결과</h3>{progress.recent_improvements.map((event,i)=><p className="knowledge-improvement" key={event.created_at+i}><CheckCircle2 size={14}/><span>{event.description}<small>{time(event.created_at)}</small></span></p>)}{!progress.recent_improvements.length&&<p>아직 기록된 개선 결과가 없습니다.</p>}</div></div>
  </>:<p role="status">지식 준비 상태를 확인하고 있습니다…</p>}
  <form className="knowledge-plan" onSubmit={e=>{e.preventDefault();void add();}}><label htmlFor="planned-question">팀이 앞으로 확인할 질문</label><div><input id="planned-question" value={question} maxLength={1000} onChange={e=>setQuestion(e.target.value)} placeholder="예: 온도별 retention 비교에 필요한 조건은 무엇인가요?"/><button disabled={busy||!question.trim()}><Send size={14}/>등록</button></div><small>팀에 공유되며 이 전문가의 준비 과제로 사용됩니다. 자동 새 질문은 하루 최대 5개, 질문별 보완은 같은 원천 자료에서 최대 2회입니다. 원천 자료가 바뀌면 다시 준비할 수 있습니다.</small></form>{notice&&<p role="status">{notice}</p>}
 </section>;
}

export function useResearchRequests(revision:number,team=false,enabled=true){
 const [items,setItems]=useState<ResearchRequestSummary[]>([]),[error,setError]=useState(''),[total,setTotal]=useState(0);
 useEffect(()=>{if(!enabled)return;const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
  async function refresh(){try{if(!document.hidden){const data=await get<{requests:ResearchRequestSummary[];total?:number}>('/research-requests?summary=true&'+(team?'visibility=team':'mine=true'),controller.signal);if(!controller.signal.aborted){setItems(data.requests);setTotal(data.total??data.requests.length);setError('');}}}catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:'질문을 불러오지 못했습니다.');}finally{if(!controller.signal.aborted)timer=setTimeout(refresh,15000);}}
  void refresh();return()=>{controller.abort();clearTimeout(timer);};
 },[team,revision,enabled]);
 return {items,error,total};
}

export function ResearchRequestHistory({team,privateOnly=false,revision,requests,loadError='',onOpen}:{team:boolean;privateOnly?:boolean;revision:number;requests?:ResearchRequestSummary[];loadError?:string;onOpen:(request:ResearchRequestSummary)=>void}){
 const fetched=useResearchRequests(revision,team,requests===undefined),items=requests??fetched.items,error=loadError||fetched.error;
 const visible=items.filter(r=>!privateOnly||r.visibility==='private');
 return <section aria-label={team?'팀 공유 질문':'서버에 저장된 내 질문'} className="research-request-history">{error&&<p role="alert">{error}</p>}{visible.map(r=><button key={r.id} className="research-request-row" onClick={()=>onOpen(r)}><MessageSquare size={17}/><span><strong>{r.question}</strong><small>{r.expert_names.join(' · ')} · {time(r.created_at)} · {r.visibility==='team'?'팀 공유':'나만 보기'}</small></span><em>{requestStates[r.status]}</em><ArrowUpRight size={15}/></button>)}{!visible.length&&!error&&<p className="pilot-empty">저장된 질문이 없습니다.</p>}</section>;
}

export function AnswerFeedback({request,onChanged}:{request:RequestState;onChanged:()=>void}){
 const [kind,setKind]=useState('missing_evidence'),[comment,setComment]=useState(''),[busy,setBusy]=useState(false),[notice,setNotice]=useState('');
 async function submit(){if(busy)return;setBusy(true);setNotice('');try{const result=await api<{used_for_team_learning:boolean}>('/api/pilot/knowledge/feedback',{method:'POST',body:JSON.stringify({request_id:request.id,kind,comment})});setComment('');setNotice(result.used_for_team_learning?'검토 요청을 기록했습니다. 원문 근거를 확인한 뒤 개선에 반영합니다.':'비공개 피드백을 기록했습니다. 팀 지식에는 사용하지 않습니다.');onChanged();}catch(e){setNotice(e instanceof Error?e.message:'피드백을 저장하지 못했습니다.');}finally{setBusy(false);}}
 return <details className="answer-feedback"><summary>답변 개선 의견 남기기</summary><form onSubmit={e=>{e.preventDefault();void submit();}}><label>어떤 점을 확인할까요?<select value={kind} onChange={e=>setKind(e.target.value)}><option value="missing_evidence">필요한 근거 누락</option><option value="incorrect">정확성 확인 필요</option><option value="condition_gap">비교 조건 확인 필요</option><option value="conflict_gap">결과 간 상충 가능성</option><option value="unclear">설명이 불명확함</option><option value="useful">도움이 됨</option></select></label><label>확인할 부분<textarea maxLength={2000} value={comment} onChange={e=>setComment(e.target.value)} placeholder="문장, 조건 또는 빠진 논문을 알려 주세요."/></label><p>{request.visibility==='team'&&request.learning_eligible!==false?'공유 질문의 의견은 원천 검토 과제로 연결됩니다.':'비공개 질문 또는 개인 대화·보고서 맥락을 포함한 질문의 의견은 팀 학습에서 제외됩니다.'}</p><button disabled={busy}>{busy?'기록 중…':'의견 기록'}</button>{notice&&<p role="status">{notice}</p>}</form></details>;
}
