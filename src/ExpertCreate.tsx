import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Check, X } from 'lucide-react';
import definitions from '../shared/expert_facets.json';
import { Pager } from './PilotViews';
import './ExpertCreate.css';

type Draft={name:string;overview:string;facets:Record<string,string[]>;included_ids:string[];excluded_ids:string[];enabled:boolean};
type Candidate={id:string;title:string;reason?:string};
type Preview={admitted:Candidate[];review:Candidate[];excluded:Candidate[];counts:{papers:number;claims:number;measurements:number;wikis:number;review:number}};
type SavedConfig={draft:Draft;revision:string;legacy:boolean};
const allFacets=()=>Object.fromEntries(definitions.map(f=>[f.key,[...f.values]]));
async function request<T>(path:string,method='GET',body?:unknown,signal?:AbortSignal):Promise<T>{
 const response=await fetch('/api/pilot/experts'+path,{method,signal,headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
 const data=await response.json();
 if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'입력 내용을 확인한 후 다시 시도해 주세요.');
 return data;
}
function CandidateList({title,items,action}:{title:string;items:Candidate[];action:(item:Candidate)=>React.ReactNode}){
 const [page,setPage]=useState(0);
 useEffect(()=>setPage(0),[items]);
 return <section className="expert-candidate-section"><h3>{title}<span>{items.length}편</span></h3>{items.slice(page*5,page*5+5).map(item=><div className="expert-candidate" key={item.id}><div><strong>{item.title}</strong>{item.reason&&<small>{item.reason}</small>}</div><div>{action(item)}</div></div>)}{!items.length&&<p className="pilot-muted">해당하는 논문이 없습니다.</p>}{items.length>5&&<Pager page={page} size={5} total={items.length} onPage={setPage} label={title}/>}</section>;
}
export function ExpertCreate({expertId,onClose,onSaved}:{expertId?:string;onClose:()=>void;onSaved:(expert:{id:string;name:string})=>void}){
 const modal=useRef<HTMLDialogElement>(null),requestId=useRef(crypto.randomUUID());
 const editing=!!expertId,title=editing?'전문가 설정':'전문가 추가';
 const [draft,setDraft]=useState<Draft>({name:'',overview:'',facets:allFacets(),included_ids:[],excluded_ids:[],enabled:true});
 const [stage,setStage]=useState<'define'|'preview'>('define'),[result,setResult]=useState<Preview|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [loading,setLoading]=useState(editing),[revision,setRevision]=useState(''),[legacy,setLegacy]=useState(false);
 useEffect(()=>{modal.current?.showModal();},[]);
 useEffect(()=>{
  if(!expertId)return;
  const ctrl=new AbortController();
  request<SavedConfig>('/'+encodeURIComponent(expertId),'GET',undefined,ctrl.signal).then(data=>{
   setDraft(data.draft);setRevision(data.revision);setLegacy(data.legacy);
  }).catch(e=>{if(e.name!=='AbortError')setError(e.message);}).finally(()=>{if(!ctrl.signal.aborted)setLoading(false);});
  return()=>ctrl.abort();
 },[expertId]);
 const ready=!!draft.name.trim()&&!!draft.overview.trim()&&(!editing||!!revision);
 function update(change:Partial<Draft>){setDraft(old=>({...old,...change}));setResult(null);setError('');}
 function select(key:string,values:string[]){update({facets:{...draft.facets,[key]:values}});}
 async function preview(next=draft){setBusy(true);setError('');setResult(null);try{const data=await request<Preview>('/preview','POST',next);setResult(data);setStage('preview');}catch(e){setError(e instanceof Error?e.message:'지식을 확인하지 못했습니다.');}finally{setBusy(false);}}
 async function decide(id:string,include:boolean){const next={...draft,included_ids:include?[...new Set([...draft.included_ids,id])]:draft.included_ids.filter(x=>x!==id),excluded_ids:include?draft.excluded_ids.filter(x=>x!==id):[...new Set([...draft.excluded_ids,id])]};setDraft(next);await preview(next);}
 async function save(){
  if(!result)return;setBusy(true);setError('');
  try{const saved=await request<{id:string;name:string}>(editing?'/'+encodeURIComponent(expertId!):'',editing?'PUT':'POST',editing?{...draft,revision}:{...draft,request_id:requestId.current});onSaved(saved);}
  catch(e){setError(e instanceof Error?e.message:'전문가를 저장하지 못했습니다.');}finally{setBusy(false);}
 }
 const group=(keys:string[])=>definitions.filter(f=>keys.includes(f.key)).map(f=>{
  const selected=draft.facets[f.key],all=selected.length===f.values.length;
  return <fieldset className="expert-create-facet" key={f.key}><legend>{f.label}</legend><div className="expert-facet-tools"><span>{all?'전체 선택 · 제한 없음':selected.length?'선택 '+selected.length+'개':'선택 없음 · 자동 편입 없음'}</span><button type="button" disabled={all} aria-label={f.label+' 전체 선택'} onClick={()=>select(f.key,[...f.values])}>전체 선택</button><button type="button" disabled={!selected.length} aria-label={f.label+' 전체 해제'} onClick={()=>select(f.key,[])}>전체 해제</button></div><div className="expert-create-options">{f.values.map(value=><label key={value}><input type="checkbox" checked={selected.includes(value)} onChange={()=>select(f.key,selected.includes(value)?selected.filter(x=>x!==value):[...selected,value])}/>{value}</label>)}</div></fieldset>;
 });
 return <dialog ref={modal} className="pilot-dialog expert-create-dialog" aria-label={title} onCancel={e=>{if(busy)e.preventDefault();else onClose();}}>
 <header><div><span>EXPERT PROFILE</span><h2>{title}</h2></div><button type="button" className="icon-button" disabled={busy} aria-label={title+' 닫기'} onClick={onClose}><X size={20}/></button></header>
 <form onSubmit={e=>{e.preventDefault();if(ready&&!busy&&!loading){if(stage==='define'||!result)preview();else save();}}}>
 <div className="expert-create-body">
 {loading?<p role="status" className="pilot-muted">저장된 설정을 불러오고 있습니다…</p>:<>
 <div className="expert-create-steps"><span className={stage==='define'?'active':''}>01 개요와 분류</span><ArrowRight size={14}/><span className={stage==='preview'?'active':''}>02 지식 확인</span></div>
 {error&&<p className="pilot-error" role="alert">{error}</p>}
 {stage==='define'?<fieldset className="expert-create-definition" disabled={busy||!ready&&editing&&!revision}>
 {legacy&&<p className="expert-config-note">이 전문가는 분류 조합 기록이 없어 현재 담당 논문을 직접 편입으로 보존했습니다. 자동 편입을 사용하려면 분류를 선택해 주세요.</p>}
 <label className="expert-create-field">전문가 이름<input required maxLength={60} autoFocus value={draft.name} onChange={e=>update({name:e.target.value})} placeholder="예: DRAM Reliability"/></label>
 <label className="expert-create-field">전문가 개요<textarea required rows={3} maxLength={1000} value={draft.overview} onChange={e=>update({overview:e.target.value})} placeholder="전문 분야와 연구 관점, 도움을 줄 수 있는 질문을 소개하세요."/></label>
 <h3>논문 분류 조합</h3><p className="pilot-muted">같은 항목 안은 OR, 항목 사이는 AND입니다. 전체 선택은 제한 없음이며, 개요를 수정해도 분류는 바뀌지 않습니다.</p>
 {group(['platform','focus'])}<details className="expert-create-advanced"><summary>세부 분류</summary>{group(['layer','application','method'])}</details>
 {(draft.included_ids.length>0||draft.excluded_ids.length>0)&&<section className="expert-override-summary"><div><strong>직접 편입 {draft.included_ids.length}편 · 제외 {draft.excluded_ids.length}편</strong><p>직접 편입·제외가 분류보다 우선됩니다. 다음 단계에서 논문을 확인할 수 있습니다.</p></div><div><button type="button" disabled={!draft.included_ids.length} onClick={()=>update({included_ids:[]})}>직접 편입 해제</button><button type="button" disabled={!draft.excluded_ids.length} onClick={()=>update({excluded_ids:[]})}>제외 해제</button></div></section>}
 <label className="expert-create-enabled"><input type="checkbox" checked={draft.enabled} onChange={e=>update({enabled:e.target.checked})}/>질문할 수 있는 전문가로 사용</label>
 </fieldset>:result&&<><h3>{draft.name}의 지식</h3><p className="pilot-muted">저장 후 연결할 논문과 지식입니다. 판단이 필요한 논문은 편입 전까지 질문 근거에 포함되지 않습니다.</p><div className="expert-create-stats">{[[result.counts.papers,'담당 논문'],[result.counts.claims,'연결된 주장'],[result.counts.wikis,'재사용할 공통 Wiki'],[result.counts.review,'검토 대기']].map(([n,label])=><div key={label}><strong>{n}</strong><span>{label}</span></div>)}</div>
 <CandidateList title="담당 논문" items={result.admitted} action={p=><button type="button" disabled={busy} onClick={()=>decide(p.id,false)}>제외</button>}/>
 <CandidateList title="판단이 필요한 논문" items={result.review} action={p=><><button type="button" disabled={busy} onClick={()=>decide(p.id,true)}>편입</button><button type="button" disabled={busy} onClick={()=>decide(p.id,false)}>제외</button></>}/>
 {!!result.excluded.length&&<details><summary>제외한 논문 {result.excluded.length}편</summary><CandidateList title="제외 논문" items={result.excluded} action={p=><button type="button" disabled={busy} onClick={()=>decide(p.id,true)}>편입</button>}/></details>}
 {!result.counts.papers&&<p className="pilot-local-note">담당 논문 없이도 저장할 수 있습니다. 관련 논문이 연결되기 전에는 근거를 찾을 수 없습니다.</p>}
 </>}
 </>}
 </div><footer className="expert-create-footer"><button type="button" className="secondary" disabled={busy} onClick={()=>stage==='preview'?setStage('define'):onClose()}>{stage==='preview'?'개요·분류 수정':'취소'}</button><button className="primary" type="submit" disabled={!ready||busy||loading}>{busy?'확인 중…':stage==='define'||!result?<>지식 구성 미리보기<ArrowRight size={15}/></>:<><Check size={15}/>{editing?'변경사항 저장':'전문가 생성'}</>}</button></footer>
 </form></dialog>;
}

