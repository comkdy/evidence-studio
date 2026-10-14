import { useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { api } from './identity';
import './WikiRefresh.css';

type Job={id:number;state:'queued'|'running'|'done'|'failed';error:string};

export function WikiRefresh({expertId,expertName,disabled=false,onCompleted}:{expertId?:string;expertName?:string;disabled?:boolean;onCompleted:()=>void}){
 const [jobId,setJobId]=useState<number|null>(null),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[failed,setFailed]=useState(false);
 const completed=useRef(onCompleted);completed.current=onCompleted;
 useEffect(()=>{
  if(jobId===null)return;
  const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
  async function poll(){
   try{
    const job=await api<Job>('/api/pilot/wiki-maintenance/jobs/'+jobId,{signal:controller.signal});
    if(controller.signal.aborted)return;
    if(job.state==='done'||job.state==='failed'){
     setBusy(false);setJobId(null);setFailed(job.state==='failed');
     setMessage(job.state==='done'?'변경사항 확인·반영 완료':job.error||'갱신을 완료하지 못했습니다.');
     if(job.state==='done')completed.current();
    }else{setMessage(job.state==='running'?'Wiki 변경사항을 확인하고 있습니다…':'갱신 요청 대기 중…');timer=setTimeout(poll,2000);}
   }catch(error){if(!controller.signal.aborted){setBusy(false);setJobId(null);setFailed(true);setMessage(error instanceof Error?error.message:'진행 상태를 확인하지 못했습니다.');}}
  }
  void poll();return()=>{controller.abort();clearTimeout(timer);};
 },[jobId]);
 async function refresh(){
  setBusy(true);setFailed(false);setMessage('갱신을 요청하고 있습니다…');
  try{
   const result=await api<{job_id:number}>(expertId?'/api/pilot/experts/'+encodeURIComponent(expertId)+'/wiki-refresh':'/api/pilot/wiki-maintenance/common',{method:'POST'});
   setJobId(result.job_id);
  }catch(error){setBusy(false);setFailed(true);setMessage(error instanceof Error?error.message:'갱신을 요청하지 못했습니다.');}
 }
 return <div className={'wiki-refresh-control'+(expertId?' compact':'')}><button className={expertId?'expert-card-refresh':'secondary'} aria-label={expertId?expertName+' Wiki 갱신':'공통 Wiki 갱신'} disabled={disabled||busy} title={disabled?'사용 중인 전문가만 갱신할 수 있습니다.':undefined} onClick={refresh}><RefreshCw size={13}/>{busy?'갱신 중…':expertId?'갱신':'공통 Wiki 갱신'}</button>{message&&<p className={'wiki-refresh-message'+(failed?' wiki-refresh-error':'')} role={failed?'alert':'status'}>{message}</p>}</div>;
}

export function CommonWikiRefresh({onCompleted}:{onCompleted:()=>void}){
 return <section className="common-wiki-refresh" aria-label="공통 Wiki 관리"><div><h2>공통 Wiki</h2><p>공유하는 원천 지식과 주제 Wiki를 갱신합니다. 전문가별 Wiki는 아래 카드에서 갱신하세요.</p></div><WikiRefresh onCompleted={onCompleted}/></section>;
}
