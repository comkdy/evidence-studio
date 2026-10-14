import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, CircleCheck, Clock3, FileText, RefreshCw, TriangleAlert } from 'lucide-react';
import { get, time } from './pilotData';
import './ProcessingActivity.css';
import { WikiJobActivity } from './WikiJobActivity';
import type { WikiJob } from './wikiUsage';

type State='queued'|'running'|'completed'|'failed'|'review'|'waiting';
type Task={id:string;kind:string;label:string;state:State;stage:string;detail:string;paper_id:string|null;expert_id:string|null;updated_at:string;superseded:boolean};
export type Processing={watcher:{state:string;checked_at:string|null;interval_seconds:number;detail:string};counts:Record<State,number>;review_pages:number;tasks:Task[];wiki_jobs?:WikiJob[];total_tasks:number;revision:string};
const labels:Record<State,string>={queued:'대기',running:'처리 중',completed:'완료',failed:'문제 발생',review:'검토 필요',waiting:'연결 대기'};

export function useProcessing(onChanged:()=>void){
 const [data,setData]=useState<Processing|null>(null),[error,setError]=useState(false);
 const changed=useRef(onChanged);changed.current=onChanged;
 useEffect(()=>{
  const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;let revision:string|null=null;let fetching=false;
  async function refresh(){
   if(fetching||controller.signal.aborted)return;clearTimeout(timer);
   if(!document.hidden){fetching=true;try{const next=await get<Processing>('/processing',controller.signal);if(!controller.signal.aborted){setData(next);setError(false);if(revision!==null&&revision!==next.revision)changed.current();revision=next.revision;}}catch{if(!controller.signal.aborted)setError(true);}finally{fetching=false;}}
   if(!controller.signal.aborted)timer=setTimeout(refresh,5000);
  }
  void refresh();document.addEventListener('visibilitychange',refresh);
  return()=>{controller.abort();clearTimeout(timer);document.removeEventListener('visibilitychange',refresh);};
 },[]);
 return {data,error};
}

export function ProcessingBadge({data,error,reviewPages,onOpen}:{data:Processing|null;error:boolean;reviewPages:number;onOpen:()=>void}){
 const counts=data?.counts,attention=(counts?.failed??0)+(counts?.review??0);
 const monitorIssue=data&&data.watcher.state!=='watching';
 const state=error||attention||monitorIssue?'attention':counts?.running?'running':'idle';
 const headline=error?'상태 확인 불가':monitorIssue?'감시 확인 필요':counts?.running?`${counts.running}건 처리 중`:attention?`${attention}건 확인 필요`:counts?.queued?`${counts.queued}건 대기 중`:counts?.waiting?'LLM 연결 대기':data?.tasks.length?'최근 작업 완료':data?'새 자료 대기 중':'작업 확인 중';
 const review=data?.review_pages??reviewPages;
 return <section className={'processing-badge '+state} aria-label="자료 처리 현황">
  <button type="button" className="processing-overview" onClick={onOpen}>
   <span><i/>작업 현황</span>
   <strong>{headline}</strong>
   <small>대기 {counts?.queued??'—'} · 진행 {counts?.running??'—'} · 완료 {counts?.completed??'—'}</small>
   <span className="processing-review"><span>{review?'확인 필요 '+review+'페이지':'자료 검토하기'}</span><ArrowUpRight size={14}/></span>
  </button>
 </section>;
}

export function ProcessingView({data,error,onPaper}:{data:Processing|null;error:boolean;onPaper:(id:string)=>void}){
 return <section className="processing-view" aria-label="작업 진행 현황"><div className="processing-heading"><h2>작업 진행 현황</h2><span className={data?.watcher.state==='watching'?'is-watching':''}><i/>{error?'상태 조회 실패':data?.watcher.state==='watching'?'자동 감시 중 · 60초 간격':'감시 확인 필요'}</span></div>
 <p className="processing-caption">새 논문과 전문가 지식의 처리 상태를 확인하세요. {data?.watcher.checked_at&&<>최근 자료 확인 {time(data.watcher.checked_at)}</>}</p>
 {error?<p className="pilot-error" role="alert">작업 상태를 불러오지 못했습니다. 잠시 후 자동으로 다시 확인합니다.</p>:data?.watcher.state!=='watching'&&<p className="pilot-notice">{data?.watcher.detail||'감시 상태를 확인하고 있습니다.'}</p>}
 <div className="processing-counts">{(['queued','running','completed','failed'] as const).map(s=><div key={s}><span>{labels[s]}</span><strong>{data?.counts[s]??'—'}</strong></div>)}</div>
 <WikiJobActivity jobs={data?.wiki_jobs??[]}/>
 <div className="processing-tasks">{data?.tasks.map(task=><article className={'processing-task '+task.state} key={task.id}><span className="processing-task-icon">{task.state==='running'?<RefreshCw size={16}/>:task.state==='completed'?<CircleCheck size={16}/>:task.state==='failed'||task.state==='review'?<TriangleAlert size={16}/>:<Clock3 size={16}/>}</span><div><header><strong>{task.label}</strong><span>{labels[task.state]}</span></header><p>{task.stage}{task.superseded?' · 이전 기록':''}</p>{task.detail&&<p className="processing-detail">{task.detail}</p>}<small>{time(task.updated_at)}</small>{task.paper_id&&<button onClick={()=>onPaper(task.paper_id!)}><FileText size={12}/>자료 보기</button>}</div></article>)}</div>
 {!data?.tasks.length&&<p className="pilot-muted">새 논문이 들어오거나 전문가를 생성하면 처리 내역이 여기에 표시됩니다.</p>}
 {!!data?.tasks.length&&<p className="processing-caption">진행 중이거나 확인이 필요한 작업과 최근 기록을 최대 12개 표시합니다.</p>}
 </section>;
}
