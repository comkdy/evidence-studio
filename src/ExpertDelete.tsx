import { useEffect, useRef, useState } from 'react';
import { Trash2, X } from 'lucide-react';
import type { Expert } from './pilotData';
import './ExpertDelete.css';

export type DeletedExpert={id:string;name:string;deleted_at:string};
export function ExpertDelete({expert,onClose,onDeleted}:{expert:Expert;onClose:()=>void;onDeleted:(expert:DeletedExpert)=>void}){
 const dialog=useRef<HTMLDialogElement>(null);
 const [config,setConfig]=useState<{revision:string;draft:{name:string}}|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{
  dialog.current?.showModal();
  const ctrl=new AbortController();
  fetch('/api/pilot/experts/'+encodeURIComponent(expert.id),{signal:ctrl.signal}).then(async response=>{
   const data=await response.json();if(!response.ok)throw new Error(data.detail||'전문가를 확인하지 못했습니다.');return data;
  }).then(setConfig).catch(e=>{if(e.name!=='AbortError')setError(e.message);});
  return()=>ctrl.abort();
 },[expert.id]);
 async function remove(){
  if(!config||busy)return;setBusy(true);setError('');
  try{
   const response=await fetch('/api/pilot/experts/'+encodeURIComponent(expert.id),{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:config.revision})});
   const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'삭제하지 못했습니다. 다시 시도해 주세요.');
   onDeleted(data);
  }catch(e){setError(e instanceof Error?e.message:'삭제하지 못했습니다.');}finally{setBusy(false);}
 }
 return <dialog ref={dialog} className="pilot-dialog expert-delete-dialog" aria-labelledby="expert-delete-title" aria-describedby="expert-delete-description" onCancel={e=>{if(busy)e.preventDefault();else onClose();}}>
  <header><h2 id="expert-delete-title">전문가 삭제</h2><button className="icon-button" aria-label="삭제 창 닫기" disabled={busy} onClick={onClose}><X size={19}/></button></header>
  <div className="expert-delete-body"><strong>{config?.draft.name||expert.name}</strong><p id="expert-delete-description">이 전문가를 삭제할까요? 전문가 목록과 질문 추천에서 제외됩니다.</p><p className="expert-delete-preserve">논문과 공통 지식, 기존 답변 기록은 유지되며 이 전문가의 지식 연결은 해제됩니다. 삭제 후 알림에서 실행 취소할 수 있습니다.</p>{error&&<p className="pilot-error" role="alert">{error}</p>}</div>
  <footer><button className="secondary" autoFocus disabled={busy} onClick={onClose}>취소</button><button className="expert-delete-confirm" disabled={!config||busy} onClick={remove}><Trash2 size={15}/>{busy?'삭제 중…':'전문가 삭제'}</button></footer>
 </dialog>;
}
