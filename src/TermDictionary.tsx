import { useEffect, useRef, useState } from 'react';
import { BookA, ChevronRight, Plus, Search, X } from 'lucide-react';
import { api } from './identity';
import type { Session } from './identity';
import { time } from './pilotData';
import './TermDictionary.css';

type Status='draft'|'approved'|'archived';
type Term={id:string;preferred:string;aliases:string[];status:Status;revision:number;updated_at:string};
type TermList={terms:Term[];total:number;counts:Record<Status,number>;revision:number};
type TermDetail={term:Term;history:{snapshot:Term;actor_name:string;created_at:string}[]};
type Preview={normalized:string;keywords:string[];matches:Term[];revision:number};
const labels:Record<Status,string>={approved:'승인됨',draft:'검토 대기',archived:'보관됨'};
const endpoint='/api/pilot/terminology';
const split=(value:string)=>value.split(/[\n,]/).map(s=>s.trim()).filter(Boolean);

export function TermDictionary({session}:{session:Session}){
 const manager=session.user.permissions.manage_knowledge;
 const [data,setData]=useState<TermList|null>(null),[query,setQuery]=useState(''),[status,setStatus]=useState<Status|'all'>('all'),[page,setPage]=useState(0);
 const [revision,setRevision]=useState(0),[loading,setLoading]=useState(true),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [editor,setEditor]=useState<{id?:string}|null>(null);
 const [sample,setSample]=useState('보존 시간과 VDD'),[preview,setPreview]=useState<Preview|null>(null),[previewError,setPreviewError]=useState(''),[checking,setChecking]=useState(false);
 const sampleVersion=useRef(0);
 useEffect(()=>{const controller=new AbortController();setLoading(true);setError('');const timer=setTimeout(()=>{
  api<TermList>(endpoint+'?'+new URLSearchParams({q:query,status,offset:String(page*12),limit:'12'}),{signal:controller.signal}).then(value=>{if(!controller.signal.aborted){setData(value);setLoading(false);}}).catch(e=>{if(!controller.signal.aborted){setError(e.message);setLoading(false);}});
 },180);return()=>{controller.abort();clearTimeout(timer);};},[query,status,page,revision]);
 async function check(){const version=++sampleVersion.current;setChecking(true);setPreviewError('');try{const value=await api<Preview>(endpoint+'/preview',{method:'POST',body:JSON.stringify({text:sample})});if(version===sampleVersion.current)setPreview(value);}catch(e){if(version===sampleVersion.current)setPreviewError(e instanceof Error?e.message:'적용 결과를 확인하지 못했습니다.');}finally{if(version===sampleVersion.current)setChecking(false);}}
 function saved(term:Term){setEditor(null);setPage(0);setRevision(v=>v+1);setPreview(null);sampleVersion.current++;setChecking(false);setNotice(term.status==='approved'?'승인한 용어를 저장했습니다. 다음 검색·답변과 Wiki 갱신·점검부터 적용됩니다.':term.status==='draft'?'용어 제안을 저장했습니다. 승인 전에는 검색·답변에 적용되지 않습니다.':'용어를 보관했습니다. 검색·답변에서 사용하지 않으며 변경 이력은 유지합니다.');}
 return <section className="term-dictionary">
  <div className="term-toolbar"><div><span className="term-count">승인된 용어 <strong>{data?.counts.approved??'—'}</strong></span><p>같은 뜻의 표현을 영어 대표 용어로 통일합니다.</p></div><button className="primary" onClick={()=>setEditor({})}><Plus size={16}/>{manager?'용어 등록':'용어 제안'}</button></div>
  {notice&&<p className="term-notice" role="status">{notice}</p>}
  <div className="term-filters"><label className="term-search"><Search size={17}/><input aria-label="용어 검색" placeholder="대표 용어 또는 동의어로 찾기" value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}}/></label><div className="term-tabs" aria-label="용어 상태">{(['all','approved','draft','archived'] as const).map(value=><button key={value} aria-pressed={status===value} onClick={()=>{setStatus(value);setPage(0);}}>{value==='all'?'전체':labels[value]}{value!=='all'&&data?<small>{data.counts[value]}</small>:null}</button>)}</div></div>
  {error&&<p className="term-error" role="alert">{error}<button onClick={()=>setRevision(v=>v+1)}>다시 시도</button></p>}
  {loading?<p className="pilot-muted" role="status">용어를 불러오고 있습니다…</p>:<><div className="term-list">{data?.terms.map(term=><button className="term-row" key={term.id} onClick={()=>setEditor({id:term.id})}><BookA size={19}/><span className="term-row-main"><strong>{term.preferred}<span className={'term-state '+term.status}>{labels[term.status]}</span></strong><span>{term.aliases.join(' · ')||'등록된 동의어 없음'}</span></span><ChevronRight size={17}/></button>)}</div>{data?.total===0&&<p className="pilot-empty">일치하는 용어가 없습니다. 새로운 용어를 제안해 보세요.</p>}<div className="term-pager"><span>{data?.total??0}개 용어 · 12개씩 표시</span><button disabled={page===0} onClick={()=>setPage(v=>v-1)}>이전</button><span>{page+1} / {Math.max(1,Math.ceil((data?.total??0)/12))}</span><button disabled={(page+1)*12>=(data?.total??0)} onClick={()=>setPage(v=>v+1)}>다음</button></div></>}
  <details className="term-preview"><summary>사전이 어떻게 적용되는지 확인하기</summary><p>동의어를 대표 용어로 묶어 검색과 답변에 활용합니다. 원문 인용과 수치·단위는 바꾸지 않습니다.</p><form onSubmit={e=>{e.preventDefault();void check();}}><input aria-label="용어 적용 확인 문장" value={sample} maxLength={1000} onChange={e=>{setSample(e.target.value);setPreview(null);sampleVersion.current++;setChecking(false);}}/><button className="secondary" disabled={checking||!sample.trim()}>{checking?'확인 중…':'적용 확인'}</button></form>{previewError&&<p role="alert">{previewError}</p>}{preview&&<div className="term-preview-result"><span>검색용 해석</span><strong>{preview.normalized}</strong><span>연결된 대표 용어</span><p>{preview.matches.map(t=>t.preferred).join(' · ')||'일치하는 승인 용어 없음'}</p></div>}</details>
  <p className="term-footnote">모든 사용자가 용어를 조회·제안할 수 있습니다. 관리자와 슈퍼 유저가 승인·수정하며, 기존 원문과 저장된 답변은 그대로 보존합니다.</p>
  {editor&&<TermEditor key={editor.id||'new'} id={editor.id} manager={manager} onClose={()=>setEditor(null)} onSaved={saved}/>}
 </section>;
}

function TermEditor({id,manager,onClose,onSaved}:{id?:string;manager:boolean;onClose:()=>void;onSaved:(term:Term)=>void}){
 const dialog=useRef<HTMLDialogElement>(null),requestId=useRef(crypto.randomUUID());
 const [detail,setDetail]=useState<TermDetail|null>(null),[preferred,setPreferred]=useState(''),[aliases,setAliases]=useState(''),[status,setStatus]=useState<Status>('draft');
 const [loading,setLoading]=useState(Boolean(id)),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const editable=manager||!id;
 useEffect(()=>{dialog.current?.showModal();if(!id)return;const controller=new AbortController();api<TermDetail>(endpoint+'/'+encodeURIComponent(id),{signal:controller.signal}).then(value=>{if(controller.signal.aborted)return;setDetail(value);const t=value.term;setPreferred(t.preferred);setAliases(t.aliases.join('\n'));setStatus(t.status);setLoading(false);}).catch(e=>{if(!controller.signal.aborted){setError(e.message);setLoading(false);}});return()=>controller.abort();},[id]);
 async function save(){if(busy||!editable)return;setBusy(true);setError('');try{const term=await api<Term>(endpoint+(id?'/'+encodeURIComponent(id):''),{method:id?'PUT':'POST',body:JSON.stringify({id:requestId.current,preferred,aliases:split(aliases),status,revision:detail?.term.revision??0})});onSaved(term);}catch(e){setError(e instanceof Error?e.message:'저장하지 못했습니다.');}finally{setBusy(false);}}
 return <dialog ref={dialog} className="term-dialog" aria-labelledby="term-dialog-title" onCancel={e=>{e.preventDefault();if(!busy)onClose();}} onClick={e=>{if(e.target===dialog.current&&!busy)onClose();}}>
  <header><div><small>팀 용어 사전{id&&detail?' · v'+detail.term.revision:''}</small><h2 id="term-dialog-title">{id?(editable?'용어 확인·수정':'용어 상세'):(manager?'용어 등록':'용어 제안')}</h2></div><button aria-label="용어 창 닫기" disabled={busy} onClick={onClose}><X size={21}/></button></header>
  <div className="term-dialog-body">{loading?<p role="status">용어를 불러오고 있습니다…</p>:<form id="term-form" onSubmit={e=>{e.preventDefault();void save();}}><fieldset disabled={busy||!editable||Boolean(id&&!detail)}>
   <label>대표 용어 <em>영어 원문</em><input required value={preferred} maxLength={120} onChange={e=>setPreferred(e.target.value)} placeholder="예: threshold voltage"/></label>
   <label>동의어<textarea value={aliases} onChange={e=>setAliases(e.target.value)} placeholder={'문턱 전압\nVth'} rows={3}/><small>같은 뜻의 한글 표현·영어 표현·약어를 한 줄에 하나씩 입력하세요.</small></label>
   {manager&&<label>검토 상태<select value={status} onChange={e=>setStatus(e.target.value as Status)}><option value="draft">검토 대기 · 아직 적용하지 않음</option><option value="approved">승인됨 · 검색·답변에 적용</option><option value="archived">보관됨 · 적용 중지, 이력 보존</option></select></label>}
  </fieldset><p className="term-footnote">HfO₂, 10²처럼 입력한 위·아래 첨자는 그대로 보관합니다. V, cm⁻² 등 단위만 등록할 수는 없습니다.</p></form>}
  {error&&<p className="term-error" role="alert">{error}</p>}
  {detail&&<details className="term-history"><summary>변경 이력 · 최근 {detail.history.length}건</summary>{detail.history.map(h=><article key={h.snapshot.revision}><strong>v{h.snapshot.revision} · {labels[h.snapshot.status]} · {h.snapshot.preferred}</strong><small>{h.actor_name==='seed'?'기존 표기 규칙 가져오기':h.actor_name} · {time(h.created_at)}</small><p>동의어: {h.snapshot.aliases.join(' · ')||'없음'}</p></article>)}</details>}
  </div><footer><span>{status==='approved'?'다음 검색·답변부터 적용 · Wiki는 다음 갱신 때 반영':status==='archived'?'사용을 중지하고 이력만 보관합니다.':'승인 전에는 검색·답변에 적용되지 않습니다.'}</span><div><button className="secondary" disabled={busy} onClick={onClose}>닫기</button>{editable&&<button className="primary" form="term-form" type="submit" disabled={busy||loading||Boolean(id&&!detail)}>{busy?'저장 중…':manager?'저장':'제안 제출'}</button>}</div></footer>
 </dialog>;
}
