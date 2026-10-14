import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, Check, FileText, X } from 'lucide-react';
import { api } from './identity';
import { time } from './pilotData';
import type { CorpusStatus, PageIssue } from './pilotData';
import './SourceReviews.css';

type Action='correct'|'verify'|'exclude'|'reopen';
type ReviewRecord={id:string;action:Action;proposed_text:string;reason:string;state:'pending'|'applied'|'rejected';actor_name:string;reviewer_name:string;created_at:string;decided_at:string|null;decision_note:string};
type ReviewDetail={page:PageIssue&{id:string;text:string;method:string};revision:string;original_text:string;linked_evidence:number;can_apply:boolean;problem:{characters:number;suspicious_characters:number;snippets:string[];reason:string};history:ReviewRecord[]};
const actions:Record<Action,{title:string;description:string}>={
 correct:{title:'텍스트 보완',description:'누락되거나 깨진 본문·caption·표를 원본대로 보완합니다.'},
 verify:{title:'원문과 일치함 확인',description:'추출문이 정확한 경우, 현재 텍스트를 검색에 사용합니다.'},
 exclude:{title:'검색에서 제외',description:'빈 페이지 등 검색할 내용이 없는 경우입니다. 원본은 유지합니다.'},
 reopen:{title:'다시 검토',description:'완료한 검토를 다시 엽니다. 보완 전까지 이 페이지의 원문 검색을 중단합니다.'}
};
const stateNames={pending:'반영 요청 대기',applied:'반영 완료',rejected:'반영하지 않음'};

export function SourceReviews({status,onChanged}:{status:CorpusStatus|null;onChanged:()=>void}){
 const [selected,setSelected]=useState<string|null>(null);
 function row(p:PageIssue){return <article className="source-review-row" key={p.page_id}><FileText size={17}/><div><strong>{p.title}</strong><p>PDF p. {p.page_number} · {p.status==='needs_ocr'?`텍스트 추출 부족 (${p.char_count}자)`:p.status==='excluded'?'검색 제외':p.status==='ready'?'텍스트 검토 완료':'문자 깨짐·읽기 품질 확인'}{p.pending_requests>0&&<span className="review-request-badge">수정 요청 {p.pending_requests}</span>}</p><button className="secondary" onClick={()=>setSelected(p.page_id)}>{p.status==='ready'||p.status==='excluded'?'검토 내용·이력':'원문 대조·보완'}</button></div><a href={'/api/pilot/documents/'+p.document_id+'/pdf#page='+p.page_number} target="_blank" rel="noreferrer" aria-label={p.title+' PDF '+p.page_number+'페이지 원본'}><ArrowUpRight size={16}/></a></article>;}
 return <section className="source-reviews"><div className="pilot-section-title"><h2>원문 텍스트 검토</h2><span>{status?.issues.length??'—'}페이지 확인 필요</span></div>
 <p className="source-review-intro">PDF에서 글자를 충분히 읽지 못했거나 문자가 깨진 페이지입니다. <strong>논문 내용이 틀렸다는 경고가 아닙니다.</strong> 해당 페이지는 보완 전까지 원문 검색에서 제외됩니다.</p>
 <div className="source-review-checklist"><strong>원본과 무엇을 비교하나요?</strong><ul><li>본문·figure caption이 빠지거나 문장 순서가 섞이지 않았는지</li><li>수치·부등호·위첨자·단위가 원본과 같은지</li><li>표의 행·열, parameter와 실험 조건의 연결이 맞는지</li></ul></div>
 {status?.issues.map(row)}
 {status&&!status.issues.length&&<p className="review-clear"><Check size={15}/>현재 텍스트 검토가 필요한 페이지는 없습니다.</p>}
 {!!status?.reviewed_pages?.length&&<details className="source-review-resolved"><summary>검토한 페이지와 수정 요청 · 최근 {status.reviewed_pages.length}페이지</summary>{status.reviewed_pages.map(row)}</details>}
 {selected&&<ReviewDialog pageId={selected} onClose={()=>setSelected(null)} onChanged={onChanged}/>}
 </section>;
}

function ReviewDialog({pageId,onClose,onChanged}:{pageId:string;onClose:()=>void;onChanged:()=>void}){
 const dialog=useRef<HTMLDialogElement>(null),requestId=useRef(crypto.randomUUID());
 const [data,setData]=useState<ReviewDetail|null>(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const [action,setAction]=useState<Action>('correct'),[text,setText]=useState(''),[reason,setReason]=useState(''),[confirmed,setConfirmed]=useState(false),[preview,setPreview]=useState(false);
 const [request,setRequest]=useState<ReviewRecord|null>(null),[decisionNote,setDecisionNote]=useState('');
 async function load(signal?:AbortSignal){const next=await api<ReviewDetail>('/api/pilot/page-reviews/'+encodeURIComponent(pageId),{signal});if(signal?.aborted)return;setData(next);setText(next.page.text);setAction('correct');setReason('');setConfirmed(false);setRequest(null);requestId.current=crypto.randomUUID();}
 useEffect(()=>{const controller=new AbortController();dialog.current?.showModal();void load(controller.signal).catch(e=>{if(!controller.signal.aborted)setError(e.message);});return()=>controller.abort();},[pageId]);
 function change(){setConfirmed(false);setNotice('');requestId.current=crypto.randomUUID();}
 function chooseRequest(r:ReviewRecord){setRequest(r);setAction(r.action);setText(r.action==='correct'?r.proposed_text:data?.page.text||'');setReason(r.reason);setDecisionNote('');change();}
 async function save(decision?:'apply'|'reject'){
  if(!data||busy)return;setBusy(true);setError('');setNotice('');
  try{
   const result=decision&&request?await api<{state:string}>('/api/pilot/page-review-requests/'+request.id+'/decision',{method:'POST',body:JSON.stringify({action:decision,revision:data.revision,note:decisionNote,confirmed})}):await api<{state:string}>('/api/pilot/page-reviews/'+encodeURIComponent(pageId),{method:'POST',body:JSON.stringify({request_id:requestId.current,revision:data.revision,action,text:action==='correct'?text:'',reason,confirmed})});
   onChanged();await load();setNotice(result.state==='pending'?'수정 요청을 저장했습니다. 관리자 또는 슈퍼 유저가 검토한 뒤 반영합니다.':result.state==='rejected'?'요청을 반영하지 않은 사유를 기록했습니다.':'검토 결과를 저장하고 원문 검색에 반영했습니다. 관련 Wiki 갱신도 요청했습니다.');
  }catch(e){setError(e instanceof Error?e.message:'저장하지 못했습니다.');}finally{setBusy(false);}
 }
 const pdf=data?'/api/pilot/documents/'+data.page.document_id+'/pdf#page='+data.page.page_number:'';
 return <dialog ref={dialog} className="source-review-dialog" aria-labelledby="source-review-title" onCancel={e=>{if(busy)e.preventDefault();else onClose();}}>
 <header><div><small>원문 텍스트 검토{data?' · PDF p. '+data.page.page_number:''}</small><h2 id="source-review-title">{data?.page.title||'자료를 불러오는 중'}</h2></div><button type="button" aria-label="원문 검토 닫기" onClick={onClose} disabled={busy}><X size={20}/></button></header>
 <div className="source-review-body">{error&&<div className="review-error" role="alert">{error}<button disabled={busy} onClick={()=>{setError('');void load().catch(e=>setError(e.message));}}>최신 내용 다시 열기</button></div>}{notice&&<p className="review-notice" role="status">{notice}</p>}
 {data&&<>
 <section className="review-diagnosis"><strong>{data.page.status==='ready'?'텍스트 검토 완료':data.page.status==='excluded'?'검색에서 제외한 페이지':'확인해야 할 부분'}</strong><p>{data.page.status==='ready'||data.page.status==='excluded'?'아래 이력에서 검토 내용과 사유를 확인할 수 있습니다.':data.problem.reason}</p>{data.problem.snippets.length>0&&<details><summary>깨진 문자 주변 보기 · {data.problem.suspicious_characters}개 감지</summary>{data.problem.snippets.map((s,i)=><pre key={i}>{s}</pre>)}</details>}</section>
 <div className="review-pdf-link"><a href={pdf} target="_blank" rel="noreferrer">원본 PDF p. {data.page.page_number} 열기<ArrowUpRight size={14}/></a><button onClick={()=>setPreview(v=>!v)}>{preview?'미리보기 닫기':'이 화면에서 원본 보기'}</button></div>
 {preview&&<iframe className="review-pdf" title={'원본 PDF '+data.page.page_number+'페이지'} src={pdf}/>}
 {data.history.some(r=>r.state==='pending')&&<section className="review-pending"><h3>팀원이 남긴 수정 요청</h3>{data.history.filter(r=>r.state==='pending').map(r=><div key={r.id}><span>{r.actor_name} · {actions[r.action].title}</span><button onClick={()=>chooseRequest(r)} disabled={busy}>요청 내용 검토</button></div>)}</section>}
 {request&&<p className="review-notice">{request.actor_name}님의 요청을 보고 있습니다. <button disabled={busy} onClick={()=>{setRequest(null);change();}}>내 수정안 작성</button></p>}
 <fieldset className="review-actions" disabled={busy||!!request}><legend>검토 결과</legend>{(Object.keys(actions) as Action[]).filter(a=>a!=='reopen'||['ready','excluded'].includes(data.page.status)).map(a=><label key={a}><input type="radio" name="source-review-action" checked={action===a} onChange={()=>{setAction(a);change();}}/><span><strong>{actions[a].title}</strong><small>{actions[a].description}</small></span></label>)}</fieldset>
 <label className="review-text-label">{action==='correct'?'보완할 검색용 텍스트':'현재 추출된 텍스트'}<textarea value={action==='correct'?text:data.page.text} onChange={e=>{setText(e.target.value);change();}} readOnly={action!=='correct'||!!request} disabled={busy} maxLength={150000} spellCheck={false}/></label>
 {action==='correct'&&<p className="review-help">원본에서 확인한 텍스트 또는 OCR 결과를 붙여 넣으세요. 영어 전문 용어·수치·단위를 그대로 유지하고, 표는 parameter와 값·조건의 관계가 드러나게 적어 주세요. 현재 자동 OCR 재실행은 연결되어 있지 않습니다.</p>}
 {action==='exclude'&&<p className="review-help">그림·표에 중요한 결과가 있다면 텍스트 보완을 권장합니다. 제외한 페이지도 원본과 이력은 남으며, 이 화면에서 다시 검토할 수 있습니다.</p>}
 {data.linked_evidence>0&&<p className="review-help">기존 근거 {data.linked_evidence}개가 연결되어 있습니다. 인용문 자체를 변경하거나 이 페이지를 제외하는 작업은 저장할 수 없습니다. 텍스트 검토는 claim·수치의 과학적 검증과 별개입니다.</p>}
 <label className="review-text-label">{request?'요청 사유':'수정·확인·제외 사유'}<textarea className="review-reason" value={reason} onChange={e=>{setReason(e.target.value);change();}} readOnly={!!request} disabled={busy} maxLength={1000} placeholder="예: 표의 retention 값과 단위가 누락되어 원본과 대조하여 보완"/></label>
 <label className="review-confirm"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e=>setConfirmed(e.target.checked)}/>원본 PDF의 해당 페이지를 직접 확인하고, 위 내용을 대조했습니다.</label>
 {!data.can_apply&&<p className="review-help">일반 유저의 수정안은 요청으로 저장됩니다. 관리자·슈퍼 유저가 검토한 뒤 팀의 검색 자료에 반영합니다.</p>}
 {request&&data.can_apply&&<label className="review-text-label">반영하지 않을 경우 사유<input value={decisionNote} onChange={e=>setDecisionNote(e.target.value)} maxLength={1000} disabled={busy}/></label>}
 <details className="review-history"><summary>원래 추출문과 변경 이력 · 최근 {data.history.length}건</summary><details><summary>최초 추출문 보기</summary><pre>{data.original_text||'추출된 텍스트 없음'}</pre></details>{data.history.map(r=><article key={r.id}><strong>{actions[r.action].title} · {stateNames[r.state]}</strong><small>{r.actor_name} · {time(r.created_at)}{r.decided_at?' · 처리 '+r.reviewer_name+' '+time(r.decided_at):''}</small><p>{r.reason}</p>{r.decision_note&&<p>처리 사유: {r.decision_note}</p>}{r.proposed_text&&<details><summary>제안된 텍스트</summary><pre>{r.proposed_text}</pre></details>}</article>)}</details>
 </>}
 </div><footer><span>{busy?'검색에 반영하고 있습니다…':'원본 PDF는 그대로 유지됩니다.'}</span><div>{request&&data?.can_apply?<><button className="secondary" disabled={busy||decisionNote.trim().length<3} onClick={()=>save('reject')}>반영하지 않음</button><button className="primary" disabled={busy||!confirmed} onClick={()=>save('apply')}>요청 반영</button></>:!request&&<button className="primary" disabled={!data||busy||!confirmed||reason.trim().length<3} onClick={()=>save()}>{data?.can_apply?'검토 결과 저장':'수정 요청 보내기'}</button>}</div></footer>
 </dialog>;
}
