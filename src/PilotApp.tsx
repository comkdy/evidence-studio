import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, BookOpen, Bookmark, ChevronRight, CircleHelp, Clock3, Copy, FileText, Layers3, Lock, Menu, MessageSquare, Plus, Search, Settings2, Share2, Trash2, Undo2, Upload, Users, X } from 'lucide-react';
import { roleLabels } from './access';
import { CommonWikiRefresh, WikiRefresh } from './WikiRefresh';
import { ProcessingBadge, ProcessingView, useProcessing } from './ProcessingActivity';
import { TermDictionary } from './TermDictionary';
import { BookA } from 'lucide-react';
import { api, displayName } from './identity';
import type { Session } from './identity';
import { AccessDenied, LoginGate, ProfileCard, UserManagement } from './AccountViews';
import { get, kinds, personalKey, readHistory, readBookmarks, readView, time, viewHash, viewNames } from './pilotData';
import { scientificNotation as numberText } from './scientificNotation';
import type { CorpusStatus, Detail, Expert, Paper, PilotView, Recommendation, SavedSearch, SearchResult, Summary, Wiki } from './pilotData';
import { AbstractOverview, ClaimCard, GuideView, Pager, StatusView, WikiCard } from './PilotViews';
import { ExpertCreate } from './ExpertCreate';
import { ResearchAnswerView } from './ResearchAnswerView';
import { answerCopyText, researchModeLabels, savedResearchMode } from './researchAnswer';
import { boundedPage, reconcileExpertScope } from './workspaceState';
import { ExpertDelete } from './ExpertDelete';
import type { DeletedExpert } from './ExpertDelete';
import { CategoryChips, LibraryCategories } from './LibraryCategories';
import type { CategoryCounts, CategorySelection } from './LibraryCategories';
import './PilotApp.css';
import './PilotLayout.css';
const descriptions:Record<PilotView,string>={
 search:'질문에 맞는 전문가를 자동으로 선택하고, 축적된 Wiki와 근거 논문을 바탕으로 답변합니다.',
 library:'논문의 주장과 주요 수치를 원문과 함께 읽어보세요.',
 wiki:'공통 지식을 바탕으로 전문가별 지식을 축적합니다. Wiki의 내용과 근거, 마지막 갱신 시점과 변경 이력을 확인하세요.',
 team:'공유를 선택한 질문과 사용한 근거를 살펴보세요.',
 personal:'내 질문, 저장한 논문과 휴지통을 모아 봅니다.',
 status:'읽을 수 있는 논문과 확인이 필요한 자료, 지식 반영 기록입니다.',
 settings:'지식에 연결된 전문가의 개요와 담당 범위입니다.',
 users:'사용자의 권한과 앱 이용 상태를 관리합니다.',
 dictionary:'전문 용어의 의미와 표기를 맞추고, 같은 개념을 일관되게 찾아보세요.',
 guide:'질문부터 원문 확인까지, Evidence Studio를 사용하는 방법입니다.'
};
export default function EvidenceStudio(){
 const [session,setSession]=useState<Session|null>(null),[error,setError]=useState('');
 async function refresh(){try{const value=await api<Session>('/api/auth/session');setSession(value);setError('');}catch(e){setSession(null);setError(e instanceof Error?e.message:'로그인 상태를 확인하지 못했습니다.');}}
 useEffect(()=>{refresh();window.addEventListener('focus',refresh);return()=>window.removeEventListener('focus',refresh);},[]);
 if(!session)return <LoginGate error={error} retry={()=>{setError('');refresh();}}/>;
 return <ResearchWorkspace key={session.user.employee_id} session={session}/>;
}
function ResearchWorkspace({session}:{session:Session}){
 const [view,setView]=useState<PilotView>(readView),[mobile,setMobile]=useState(false);
 const [summary,setSummary]=useState<Summary|null>(null),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [query,setQuery]=useState(''),[submitted,setSubmitted]=useState(''),[expert,setExpert]=useState(''),[page,setPage]=useState(0);
 const [library,setLibrary]=useState<{papers:Paper[];total:number;facet_counts:CategoryCounts}>({papers:[],total:0,facet_counts:{}});
 const [categories,setCategories]=useState<CategorySelection>({}),[expertEditor,setExpertEditor]=useState<{id?:string}|null>(null);
 const [deleteTarget,setDeleteTarget]=useState<Expert|null>(null),[lastDeleted,setLastDeleted]=useState<DeletedExpert|null>(null),[restoring,setRestoring]=useState(false);
 const isExpertManager=session.user.permissions.manage_experts;
 const [wikis,setWikis]=useState<{wikis:Wiki[];total:number}>({wikis:[],total:0}),[status,setStatus]=useState<CorpusStatus|null>(null);
 const [busy,setBusy]=useState(false),[detail,setDetail]=useState<Detail|null>(null);
 const [dataRevision,setDataRevision]=useState(0);
 const [selectedExperts,setSelectedExperts]=useState<string[]>([]),[selectionMode,setSelectionMode]=useState<'auto'|'manual'>('auto');
 const [search,setSearch]=useState<SearchResult|null>(null),[searchTitle,setSearchTitle]=useState(''),[searchExpertNames,setSearchExpertNames]=useState<string[]>([]);
 const [searchTask,setSearchTask]=useState<'recommend'|'answer'|null>(null);
 const [visibility,setVisibility]=useState<'team'|'private'>('team'),[history,setHistory]=useState<SavedSearch[]>(()=>readHistory(session.user.employee_id)),[savedId,setSavedId]=useState<string|null>(null);
 const [personalFilter,setPersonalFilter]=useState<'all'|'private'|'trash'|'bookmarks'>('all'),[bookmarks,setBookmarks]=useState(()=>readBookmarks(session.user.employee_id));
 const dialog=useRef<HTMLDialogElement>(null),selection=useRef(0),searchEpoch=useRef(0);
 const searchController=useRef<AbortController|null>(null),viewRef=useRef(view);
 const questionBusy=searchTask!==null;
 const activeExperts=summary?.experts.filter(e=>selectedExperts.includes(e.id)&&e.enabled!==0)||[];
 const browsingExpert=summary?.experts.find(e=>e.id===expert);
 const needsReview=summary?.page_states.filter(s=>s.status!=='ready'&&s.status!=='excluded').reduce((n,s)=>n+s.count,0)||0;
 function cancelSearch(){searchEpoch.current++;searchController.current?.abort();searchController.current=null;setSearchTask(null);}
 function navigate(next:PilotView){cancelSearch();close();viewRef.current=next;setView(next);location.hash=viewHash(next);setMobile(false);setQuery('');setSubmitted('');setPage(0);setError('');setBusy(false);}
 function newQuestion(){setSearch(null);setSearchTitle('');setSavedId(null);setSelectedExperts([]);setExpert('');setSelectionMode('auto');navigate('search');}
 useEffect(()=>{const change=()=>{if(location.hash==='#main')return;const next=readView();if(next!==viewRef.current){cancelSearch();close();viewRef.current=next;setPage(0);setQuery('');setSubmitted('');}setView(next);setMobile(false);if(location.hash!==viewHash(next))window.history.replaceState(null,'',viewHash(next));};change();window.addEventListener('hashchange',change);return()=>{window.removeEventListener('hashchange',change);searchEpoch.current++;searchController.current?.abort();};},[]);
 function refreshData(){setDataRevision(value=>value+1);}
 const processing=useProcessing(refreshData);
 useEffect(()=>{window.addEventListener('focus',refreshData);return()=>window.removeEventListener('focus',refreshData);},[]);
 useEffect(()=>{const ctrl=new AbortController();get<Summary>('/summary',ctrl.signal).then(value=>{if(!ctrl.signal.aborted)setSummary(value);}).catch(e=>{if(!ctrl.signal.aborted)setError(e.message);});return()=>ctrl.abort();},[view,dataRevision]);
 useEffect(()=>{
  if(!summary)return;
  setSelectedExperts(old=>{const next=reconcileExpertScope(summary.experts,old,'').selectedIds;return old.length===next.length&&old.every((id,i)=>id===next[i])?old:next;});
  setExpert(old=>reconcileExpertScope(summary.experts,[],old).filterId);
 },[summary]);
 useEffect(()=>{
  const ctrl=new AbortController();setBusy(true);setError('');
  const params=new URLSearchParams({q:submitted,expert,facets:JSON.stringify(categories),offset:String(page*(view==='wiki'?6:8)),limit:String(view==='wiki'?6:8)});
  const task=view==='library'?get<typeof library>('/papers?'+params,ctrl.signal).then(data=>{if(!ctrl.signal.aborted){setLibrary(data);setPage(boundedPage(page,data.total,8));}})
   :view==='wiki'?get<typeof wikis>('/wikis?'+params,ctrl.signal).then(data=>{if(!ctrl.signal.aborted){setWikis(data);setPage(boundedPage(page,data.total,6));}})
   :view==='status'?get<CorpusStatus>('/status',ctrl.signal).then(setStatus):Promise.resolve();
  task.catch(e=>{if(e.name!=='AbortError')setError(e.message);}).finally(()=>{if(!ctrl.signal.aborted)setBusy(false);});return()=>ctrl.abort();
 },[view,submitted,expert,page,categories,dataRevision]);
 useEffect(()=>{if(detail&&!dialog.current?.open)dialog.current?.showModal();else if(!detail)dialog.current?.close();},[detail]);
 useEffect(()=>{if(!notice)return;const timer=setTimeout(()=>setNotice(''),5000);return()=>clearTimeout(timer);},[notice]);
 useEffect(()=>{try{localStorage.setItem(personalKey(session.user.employee_id,'history'),JSON.stringify(history));}catch{setNotice('브라우저 저장 공간이 부족해 질문 기록을 저장하지 못했습니다.');}},[history,session.user.employee_id]);
 useEffect(()=>{const key=(e:KeyboardEvent)=>{if((e.ctrlKey||e.metaKey)&&e.key==='k'){e.preventDefault();navigate('library');}};window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);},[]);
 function toggleBookmark(id:string){setBookmarks(old=>{const next=old.includes(id)?old.filter(x=>x!==id):[...old,id];try{localStorage.setItem(personalKey(session.user.employee_id,'bookmarks'),JSON.stringify(next));}catch{setNotice('현재 화면에 저장했습니다. 브라우저 저장 공간은 사용할 수 없습니다.');}return next;});}
 async function openPaper(id:string){const request=++selection.current;try{const data=await get<Detail>('/papers/'+encodeURIComponent(id));if(request===selection.current)setDetail(data);}catch(e){if(request===selection.current)setError(String(e));}}
 function close(){selection.current++;setDetail(null);}
 async function askQuestion(text=query){
  const question=text.trim();
  if(!question||!summary||questionBusy||searchController.current)return;
  if(selectionMode==='manual'&&!activeExperts.length){setError('질문할 수 있는 전문가를 선택해 주세요.');return;}
  const request=++searchEpoch.current,controller=new AbortController();searchController.current=controller;setSearchTask(selectionMode==='auto'?'recommend':'answer');setError('');setSearchTitle(question);setSearch(null);setSavedId(null);
  const requestedVisibility=visibility;
  try{
   let contributors=activeExperts.map(e=>({id:e.id,name:e.name}));
   if(selectionMode==='auto'){
    const recommendation=await get<Recommendation>('/recommend?q='+encodeURIComponent(question),controller.signal);
    if(request!==searchEpoch.current||controller.signal.aborted)return;
    const available=new Set(summary.experts.filter(e=>e.enabled!==0).map(e=>e.id));
    contributors=recommendation.experts.filter((e,i,all)=>available.has(e.id)&&all.findIndex(item=>item.id===e.id)===i).slice(0,2).map(e=>({id:e.id,name:e.name}));
    setSelectedExperts(contributors.map(e=>e.id));
    if(!contributors.length){setSelectionMode('manual');setError('질문에 맞는 전문가를 자동으로 찾지 못했습니다. 아래에서 전문가를 직접 선택해 주세요.');return;}
   }
   const ids=contributors.map(e=>e.id),names=contributors.map(e=>e.name);
   setSearchTask('answer');
   const response=await fetch('/api/pilot/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,expert_ids:ids}),signal:controller.signal});if(!response.ok){const e=await response.json();throw new Error(typeof e.detail==='string'?e.detail:'답변을 준비하지 못했습니다.');}const data:SearchResult=await response.json();if(request!==searchEpoch.current||controller.signal.aborted)return;
   setSearch(data);setSearchExpertNames(names);
   const record:SavedSearch={id:crypto.randomUUID(),question,expertIds:ids,expertNames:names,createdAt:new Date().toISOString(),visibility:requestedVisibility,mode:data.research?.mode??'deep',deleted:false,result:structuredClone(data)};
   setHistory(old=>[record,...old].slice(0,100));setSavedId(record.id);
  }catch(e){if(request===searchEpoch.current&&!controller.signal.aborted)setError(String(e));}finally{if(request===searchEpoch.current){setSearchTask(null);searchController.current=null;}}
 }
 function openHistory(r:SavedSearch){setSearch(structuredClone(r.result));setSearchTitle(r.question);setSearchExpertNames(r.expertNames);setSelectedExperts(summary?reconcileExpertScope(summary.experts,r.expertIds,'').selectedIds:[]);setSavedId(r.id);navigate('search');}
 function changeRecord(id:string,change:Partial<SavedSearch>){setHistory(old=>old.map(r=>r.id===id?{...r,...change}:r));}
 async function copyResult(){if(!search)return;const evidence=['논문에서 찾은 근거',...search.claims.flatMap(c=>[numberText(c.summary),numberText(c.scope),...c.measurements.flatMap(m=>[m.label+': '+numberText(m.raw_value)+' ('+kinds[m.kind]+')',...m.conditions.map(v=>v.name+': '+numberText(v.raw_value??'미기재'))]),...c.sources.map(s=>'p. '+s.page_number+' '+s.quote)]),...search.hits.flatMap(h=>[h.title+' · p. '+h.page_number,h.text])];const text=answerCopyText(searchTitle,searchExpertNames,search,evidence);try{await navigator.clipboard.writeText(text);setNotice('답변·한계·원문 근거를 복사했습니다.');}catch{setNotice('브라우저에서 복사할 수 없습니다. 원문을 선택해서 복사해 주세요.');}}
 function showExpert(e:Expert){setExpert(e.id);navigate('wiki');}
 function showExpertPapers(e:Expert){setExpert(e.id);setCategories({});navigate('library');}
 function askExpert(e:Expert){if(e.enabled===0)return;newQuestion();setSelectedExperts([e.id]);setSelectionMode('manual');setQuery(e.name+' 분야의 주요 결과와 적용 한계를 비교해 줘');}
 function changeCategories(next:CategorySelection){setCategories(next);setPage(0);}
 function savedExpert(e:{id:string;name:string}){const editing=!!expertEditor?.id;setExpertEditor(null);cancelSearch();refreshData();if(!editing){setExpert(e.id);navigate('wiki');}setNotice(editing?e.name+' 전문가 설정을 저장했습니다.':e.name+' 전문가를 추가했습니다. 연결된 지식을 확인하세요.');}
 function deletedExpert(e:DeletedExpert){setDeleteTarget(null);setLastDeleted(e);cancelSearch();setSummary(old=>old?{...old,experts:old.experts.filter(x=>x.id!==e.id)}:old);setSelectedExperts(old=>old.filter(id=>id!==e.id));if(expert===e.id)setExpert('');refreshData();}
 async function undoExpertDeletion(){if(!lastDeleted||restoring)return;setRestoring(true);setError('');try{const response=await fetch('/api/pilot/experts/'+encodeURIComponent(lastDeleted.id)+'/restore',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({deleted_at:lastDeleted.deleted_at})});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'복원하지 못했습니다.');setLastDeleted(null);refreshData();setNotice(data.name+' 전문가 삭제를 취소했습니다.');}catch(e){setError(e instanceof Error?e.message:'복원하지 못했습니다.');}finally{setRestoring(false);}}
 function exportPage(){const csv='\uFEFF'+[['title','paper_id','claims'],...library.papers.map(p=>[p.title,p.id,p.claim_count])].map(r=>r.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\r\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8;'}));const a=document.createElement('a');a.href=url;a.download='evidence-studio-visible-papers.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 const record=savedId?history.find(r=>r.id===savedId):undefined;
 const visibleHistory=history.filter(r=>view==='team'?!r.deleted&&r.visibility==='team':personalFilter==='trash'?r.deleted:!r.deleted&&(personalFilter!=='private'||r.visibility==='private'));
 return <div className="pilot-shell pilot-layout">
  {mobile&&<button className="pilot-shade" aria-label="메뉴 닫기" onClick={()=>setMobile(false)}/>}
  <aside className={'pilot-sidebar '+(mobile?'is-open':'')}>
   <a className="pilot-side-brand" href="#library"><Layers3 size={25}/><span>Evidence Studio</span></a>
   <button className="pilot-new" onClick={newQuestion}><Plus size={17}/>새 질문 시작<ArrowUpRight size={15}/></button>
   <div className="pilot-nav-label">RESEARCH</div>
   <nav aria-label="주 메뉴">{([['search',MessageSquare],['wiki',BookOpen],['library',FileText],['dictionary',BookA],['team',Users]] as const).map(([id,Icon])=><button key={id} aria-current={view===id?'page':undefined} onClick={()=>navigate(id)}><Icon size={18}/>{viewNames[id]}{id==='library'&&<small>{summary?.counts.papers??'—'}</small>}</button>)}</nav>
   <div className="pilot-recent-title"><span>내 최근 질문</span><Clock3 size={13}/></div><div className="pilot-recent">{history.filter(r=>!r.deleted).slice(0,5).map(r=><button key={r.id} onClick={()=>openHistory(r)}><MessageSquare size={13}/><span>{r.question}</span></button>)}{!history.some(r=>!r.deleted)&&<p>질문한 근거를 여기서 다시 열 수 있습니다.</p>}</div>
   <div className="pilot-side-bottom"><ProcessingBadge data={processing.data} error={processing.error} reviewPages={needsReview} onOpen={()=>navigate('status')}/>
    {isExpertManager&&<button onClick={()=>navigate('settings')} aria-current={view==='settings'?'page':undefined}><Settings2 size={17}/>전문가 설정</button>}
    {session.user.permissions.manage_users&&<button onClick={()=>navigate('users')} aria-current={view==='users'?'page':undefined}><Users size={17}/>유저 관리</button>}
    <button onClick={()=>navigate('guide')}><CircleHelp size={17}/>사용 가이드</button>
    <button className="pilot-profile" onClick={()=>navigate('personal')} aria-current={view==='personal'?'page':undefined}><span className="user-avatar">{displayName(session).slice(0,1)}</span><span>{displayName(session)}<small>{roleLabels[session.user.role]} · 내 페이지</small></span><ChevronRight size={15}/></button>
   </div>
  </aside>
  <div className="pilot-workspace"><header className="pilot-top"><button className="pilot-menu-toggle" aria-label="메뉴 열기" onClick={()=>setMobile(true)}><Menu size={21}/></button><strong>{viewNames[view]}</strong><button className="account-session" onClick={()=>navigate('personal')}><span>{displayName(session)}</span><span className={'account-role '+session.user.role}>{roleLabels[session.user.role]}</span>{session.mode==='local_admin'&&<small>임시 접속</small>}</button></header>
  <main id="main" tabIndex={-1} className="pilot-main">
   {notice&&<div className="pilot-notice" role="status">{notice}</div>}{error&&<div className="pilot-error" role="alert">{error}<button onClick={()=>window.location.reload()}>다시 시도</button></div>}
   {view==='settings'&&isExpertManager&&lastDeleted&&<div className="expert-delete-notice" role="status"><span>{lastDeleted.name} 전문가를 삭제했습니다.</span><button disabled={restoring} onClick={undoExpertDeletion}>{restoring?'복원 중…':'실행 취소'}</button><button disabled={restoring} aria-label="삭제 알림 닫기" onClick={()=>setLastDeleted(null)}><X size={15}/></button></div>}
   <div className="pilot-page-heading"><div className="pilot-hero"><div className="eyebrow">EVIDENCE STUDIO</div><h1>{view==='search'?'읽어 둔 논문에서 근거를 찾으세요.':viewNames[view]}</h1><p>{descriptions[view]}</p></div>
    {view==='library'&&<div className="pilot-header-actions"><button className="secondary" disabled={busy} onClick={refreshData}>새로고침</button><button className="primary" disabled title="논문 업로드 기능을 준비 중입니다."><Upload size={15}/>논문 추가</button></div>}
    {(view==='team'||view==='personal')&&<button className="primary" onClick={newQuestion}><Plus size={15}/>새 질문</button>}
   </div>
   {(view==='library'||view==='status')&&<div className="pilot-compact-stats"><span><strong>{summary?.curated_papers??'—'}</strong>편 구조화</span><span><strong>{summary?.counts.claims??'—'}</strong>개 주장</span><span><strong>{summary?.counts.measurements??'—'}</strong>개 수치</span><span><strong>{summary?.counts.condition_values??'—'}</strong>개 조건 항목</span></div>}

   {view==='search'&&<>
    <form className="pilot-search" onSubmit={e=>{e.preventDefault();askQuestion();}}><Search size={19}/><input aria-label="연구 질문" disabled={questionBusy} value={query} onChange={e=>setQuery(e.target.value)} placeholder="예: FeFET endurance · OCTRAM retention"/><button className="primary" type="submit" disabled={!summary||questionBusy||!query.trim()||(selectionMode==='manual'&&!activeExperts.length)}>{questionBusy?'답변 준비 중…':'질문하기'}</button></form>
    <div className="pilot-question-options"><div className="pilot-segments" aria-label="전문가 선택 방식"><button disabled={questionBusy} aria-pressed={selectionMode==='auto'} onClick={()=>setSelectionMode('auto')}>전문가 자동 선택</button><button disabled={questionBusy} aria-pressed={selectionMode==='manual'} onClick={()=>setSelectionMode('manual')}>직접 선택</button></div><div className="pilot-segments" aria-label="질문 공개 범위"><button disabled={questionBusy} aria-pressed={visibility==='team'} onClick={()=>setVisibility('team')}><Users size={13}/>팀에 공유</button><button disabled={questionBusy} aria-pressed={visibility==='private'} onClick={()=>setVisibility('private')}><Lock size={13}/>나만 보기</button></div></div>
    <p className="pilot-muted">전문가의 Wiki와 담당 논문에서 질문에 맞는 근거를 찾습니다. 대화 목록은 이 브라우저에, 답변 당시의 근거 기록은 본인만 조회할 수 있도록 서버에 보관됩니다.</p>
    {selectionMode==='manual'&&<div className="pilot-expert-choices">{summary?.experts.filter(e=>e.enabled!==0).map(e=><label key={e.id}><input type="checkbox" disabled={questionBusy} checked={selectedExperts.includes(e.id)} onChange={()=>setSelectedExperts(old=>old.includes(e.id)?old.filter(x=>x!==e.id):[...old,e.id])}/><span>{e.name}<small>논문 {e.paper_count}편</small></span></label>)}</div>}
    {!search&&!questionBusy&&<div className="pilot-prompts">{['OCTRAM retention','FeFET endurance','CFET SRAM','NAND erase'].map(q=><button key={q} disabled={!summary} onClick={()=>{setQuery(q);askQuestion(q);}}>{q}<ArrowUpRight size={14}/></button>)}</div>}
    {questionBusy&&<p className="pilot-muted" role="status">{searchTask==='recommend'?'질문에 맞는 전문가를 찾고 있습니다…':'여러 논문의 관련 근거와 한계를 검토하고 있습니다…'}</p>}
    {search&&!questionBusy&&<section className="pilot-results"><div className="pilot-section-title"><h2>{searchTitle}</h2><button className="secondary" onClick={copyResult}><Copy size={14}/>답변과 근거 복사</button></div><div className="pilot-contributors"><BookOpen size={17}/><span>참여 전문가<strong>{searchExpertNames.join(' · ')}</strong></span></div><p className="pilot-muted">근거의 검토 상태와 적용 조건을 함께 확인하세요.</p>
     {record&&<div className="pilot-result-actions"><button onClick={()=>changeRecord(record.id,{visibility:record.visibility==='team'?'private':'team'})}>{record.visibility==='team'?<Users size={13}/>:<Lock size={13}/>} {record.visibility==='team'?'팀 공유':'나만 보기'} · 변경</button><button onClick={()=>{changeRecord(record.id,{deleted:true});setSearch(null);navigate('personal');setPersonalFilter('trash');}}><Trash2 size={13}/>휴지통</button></div>}
     <ResearchAnswerView key={search.run_id??savedId??searchTitle} result={search}/>
     <div className="pilot-section-title"><h2>질문과 연결된 주장 {search.total_claims}개</h2><span>{search.claims.length}개 표시 · 연결 수치와 조건</span></div>
     {search.claims.length?search.claims.map(c=><ClaimCard key={c.id} claim={c} onPaper={openPaper}/>):<p className="pilot-empty">선택한 전문가 범위에서 일치하는 주장을 찾지 못했습니다. 질문을 좁히거나 원문을 확인해 주세요.</p>}
     <details className="pilot-raw-results"><summary>원문 검색 결과 {search.hits.length}개 보기</summary>{search.hits.map(h=><article className="pilot-hit" key={h.id}><button onClick={()=>openPaper(h.paper_id)}>{h.title}<ArrowUpRight size={14}/></button><p>{h.text}</p><a href={'/api/pilot/documents/'+h.document_id+'/pdf#page='+h.page_number} target="_blank" rel="noreferrer">p. {h.page_number}{h.page_end&&h.page_end!==h.page_number?'–'+h.page_end:''} · 원문 PDF</a>{h.parent_context&&h.parent_context.text!==h.text&&<details className="pilot-hit-context"><summary>앞뒤 문맥 함께 보기{h.parent_context.section?' · '+h.parent_context.section:''}</summary><p>{h.parent_context.text}</p></details>}</article>)}</details>
    </section>}
   </>}

   {view==='library'&&<div className="pilot-library-layout">
    <LibraryCategories value={categories} counts={library.facet_counts} total={library.total} onChange={changeCategories}/><div className="pilot-library-results">
    <CategoryChips value={categories} onChange={changeCategories}/><div className="pilot-list-tools"><form className="pilot-search" onSubmit={e=>{e.preventDefault();setSubmitted(query);setPage(0);}}><Search size={18}/><input aria-label="논문 찾기" value={query} onChange={e=>setQuery(e.target.value)} placeholder="제목 또는 분야 검색"/><button type="submit">검색</button></form><select aria-label="논문 전문가 범위" value={expert} onChange={e=>{setExpert(e.target.value);setPage(0);}}><option value="">모든 전문가</option>{summary?.experts.map(e=><option key={e.id} value={e.id}>{e.name}{e.enabled===0?' · 사용 중지':''}</option>)}</select><button className="secondary" onClick={exportPage}>현재 목록 내보내기</button></div>
    {browsingExpert&&<div className="pilot-scope-context"><span>{browsingExpert.name}의 담당 논문{browsingExpert.enabled===0?' · 사용 중지된 전문가의 자료 열람':''}</span><button className="pilot-inline-link" onClick={()=>showExpert(browsingExpert)}>전문가 지식 보기</button><button className="pilot-inline-link" onClick={()=>{setExpert('');setPage(0);}}>전문가 필터 해제</button></div>}
    {busy?<p role="status" className="pilot-muted">논문을 불러오고 있습니다…</p>:<><div className="pilot-section-title"><h2>논문 {library.total}편</h2><span>8편씩 표시</span></div><div className="pilot-paper-grid">{library.papers.map(p=><article className="pilot-paper" key={p.id}><div className="pilot-labels">{p.topics.slice(0,3).map(t=><span title={t.basis} key={t.tag}>{t.tag}</span>)}{p.status!=='ready'&&<em>일부 페이지 확인 필요</em>}<button className="pilot-bookmark" aria-label={p.title+' 북마크'} aria-pressed={bookmarks.includes(p.id)} onClick={()=>toggleBookmark(p.id)}><Bookmark size={16} fill={bookmarks.includes(p.id)?'currentColor':'none'}/></button></div><button className="pilot-paper-title" onClick={()=>openPaper(p.id)}><h3>{p.title}</h3></button>{p.title_source==='unresolved'&&<small>제목 확인 필요 · 파일명으로 표시</small>}<AbstractOverview paper={p}/><footer><span>{p.page_count} pages</span><strong>{p.claim_count?'주장 '+p.claim_count+'개 정리':'주장 분석 전'}</strong><button aria-label={p.title+' 상세 보기'} onClick={()=>openPaper(p.id)}><ArrowUpRight size={16}/></button></footer></article>)}</div>{!library.total&&<p className="pilot-empty">일치하는 논문이 없습니다.</p>}<Pager page={page} total={library.total} size={8} onPage={setPage} label="논문"/></>}
   </div></div>}

   {view==='wiki'&&<>
    <div className="pilot-experts-overview">{summary?.experts.map(e=><button key={e.id} aria-pressed={expert===e.id} onClick={()=>{setExpert(expert===e.id?'':e.id);setPage(0);setSubmitted('');setQuery('');}}><BookOpen size={18}/><strong>{e.name}{e.enabled===0?' · 사용 중지':''}</strong><span>논문 {e.paper_count} · 주장 {e.claim_count} · Wiki {e.wiki_count}</span></button>)}</div>
    {browsingExpert&&<section className="pilot-expert-context" aria-label="선택한 전문가"><h2>{browsingExpert.name}</h2><p>{browsingExpert.overview}</p>{browsingExpert.enabled===0&&<p className="pilot-muted">사용 중지된 전문가는 질의와 Wiki 검색에서 제외됩니다. 담당 논문은 계속 열람할 수 있습니다.</p>}<div><button className="secondary" onClick={()=>showExpertPapers(browsingExpert)}>담당 논문 {browsingExpert.paper_count}편 보기</button><button className="primary" disabled={browsingExpert.enabled===0} onClick={()=>askExpert(browsingExpert)}>이 전문가에게 질문</button>{isExpertManager&&<button className="secondary" onClick={()=>setExpertEditor({id:browsingExpert.id})}><Settings2 size={14}/>설정 확인·수정</button>}</div></section>}
    <div className="pilot-list-tools"><form className="pilot-search" onSubmit={e=>{e.preventDefault();setSubmitted(query);setPage(0);}}><Search size={18}/><input aria-label="Wiki 검색" value={query} onChange={e=>setQuery(e.target.value)} placeholder="알고 싶은 사실이나 전문 용어 검색"/><button type="submit">검색</button></form>{expert&&<button className="secondary" onClick={()=>{setExpert('');setPage(0);}}>전문가 필터 해제<X size={13}/></button>}</div>
    <div className="pilot-section-title"><h2>{browsingExpert?.name||'전체 전문가'}의 Wiki</h2><span>{wikis.total}개 문서 · 6개씩 표시</span><button className="secondary" onClick={refreshData} disabled={busy}>새로고침</button></div>
    <p className="pilot-muted">지식 반영 이력은 <button className="pilot-inline-link" onClick={()=>navigate('status')}>자료 현황</button>에서 확인할 수 있습니다.</p>
    {busy?<p role="status" className="pilot-muted">Wiki를 불러오고 있습니다…</p>:<>{wikis.wikis.map(w=><WikiCard key={w.id} wiki={w} refreshKey={dataRevision} onPaper={openPaper}/>)}{!wikis.total&&<p className="pilot-empty">해당 조건의 Wiki가 없습니다.</p>}<Pager page={page} total={wikis.total} size={6} onPage={setPage} label="Wiki"/></>}
   </>}

   {(view==='team'||view==='personal')&&<>
    {view==='personal'&&<ProfileCard session={session} questions={history.filter(r=>!r.deleted).length} bookmarks={bookmarks.length}/>}
    <p className="pilot-local-note">최근 질문 100개와 북마크는 이 브라우저에 사번별로 저장됩니다. 현재 팀 공유 기록은 다른 사용자에게 전달되지 않습니다.</p>
    {view==='personal'&&<div className="pilot-segments">{([['all','내 대화'],['private','비공개'],['trash','휴지통'],['bookmarks','북마크 '+bookmarks.length]] as const).map(([id,label])=><button key={id} aria-pressed={personalFilter===id} onClick={()=>setPersonalFilter(id)}>{label}</button>)}</div>}
    {view==='personal'&&personalFilter==='bookmarks'?<div className="pilot-bookmarks">{bookmarks.map(id=><BookmarkRow key={id} id={id} onOpen={openPaper} onRemove={toggleBookmark}/>)}{!bookmarks.length&&<p className="pilot-empty">저장한 논문이 없습니다. 라이브러리에서 북마크를 눌러보세요.</p>}</div>:<>{visibleHistory.map(r=><article className="pilot-history" key={r.id}><button className="pilot-history-title" onClick={()=>openHistory(r)} disabled={r.deleted}>{r.question}<ArrowUpRight size={15}/></button><p>{r.expertNames.join(' · ')}</p><small>{time(r.createdAt)} · {researchModeLabels[savedResearchMode(r)]} 질의 · 주장 {r.result.claims.length}개 · {r.visibility==='team'?'팀 공유':'비공개'}</small><div>{!r.deleted&&<button onClick={()=>changeRecord(r.id,{visibility:r.visibility==='team'?'private':'team'})}>{r.visibility==='team'?<Lock size={13}/>:<Share2 size={13}/>} {r.visibility==='team'?'비공개로 변경':'팀에 공유'}</button>}<button onClick={()=>changeRecord(r.id,{deleted:!r.deleted})}>{r.deleted?<Undo2 size={13}/>:<Trash2 size={13}/>} {r.deleted?'복구':'삭제'}</button></div></article>)}{!visibleHistory.length&&<p className="pilot-empty">표시할 질문이 없습니다.</p>}</>}
   </>}
   {view==='status'&&<><ProcessingView data={processing.data} error={processing.error} onPaper={openPaper}/><StatusView status={status} onPaper={openPaper} onChanged={refreshData}/></>}
   {view==='settings'&&(isExpertManager?<>{session.user.permissions.refresh_wiki&&<CommonWikiRefresh onCompleted={refreshData}/>}<div className="expert-settings-heading"><p>전문가의 설정을 확인·수정하거나 새로운 전문가를 추가하세요.</p><button className="primary" onClick={()=>setExpertEditor({})}><Plus size={16}/>전문가 추가</button></div><div className="pilot-settings-grid">{summary?.experts.map(e=><article key={e.id}><header><BookOpen size={17}/><h2>{e.name}</h2>{e.enabled===0&&<span className="expert-inactive">사용 안 함</span>}<button className="expert-card-delete" aria-label={e.name+' 삭제'} title="전문가 삭제" onClick={()=>setDeleteTarget(e)}><Trash2 size={13}/><span>삭제</span></button>{session.user.permissions.refresh_wiki&&<WikiRefresh expertId={e.id} expertName={e.name} disabled={e.enabled===0} onCompleted={refreshData}/>}</header><p title={e.overview}>{e.overview}</p><div className="expert-card-counts"><span>논문 <strong>{e.paper_count}</strong></span><span>주장 <strong>{e.claim_count}</strong></span><span>Wiki <strong>{e.wiki_count}</strong></span></div><footer><button className="secondary" aria-label={e.name+' 설정 확인·수정'} onClick={()=>setExpertEditor({id:e.id})}><Settings2 size={14}/>설정 확인·수정</button><button className="expert-knowledge-link" onClick={()=>showExpert(e)}>전문가 지식<ArrowUpRight size={14}/></button></footer></article>)}</div></>:<div className="pilot-empty"><Lock size={23}/><h2>관리자와 슈퍼 유저가 접근할 수 있습니다.</h2><button onClick={()=>navigate('wiki')}>전문가 지식으로 돌아가기</button></div>)}
   {view==='users'&&(session.user.permissions.manage_users?<UserManagement session={session}/>:<AccessDenied/>)}
   {view==='dictionary'&&<TermDictionary session={session}/>}
   {view==='guide'&&<GuideView/>}
  </main></div>
  {expertEditor&&isExpertManager&&<ExpertCreate key={expertEditor.id||'new'} expertId={expertEditor.id} onClose={()=>setExpertEditor(null)} onSaved={savedExpert}/>}
  {deleteTarget&&isExpertManager&&<ExpertDelete expert={deleteTarget} onClose={()=>setDeleteTarget(null)} onDeleted={deletedExpert}/>}
  <dialog ref={dialog} className="pilot-dialog" aria-label="논문 상세" onCancel={close} onClick={e=>{if(e.target===dialog.current)close();}}>{detail&&<><header><div><span>논문 · {detail.page_count} pages</span><h2>{detail.title}</h2></div><button className="icon-button" aria-label="논문 닫기" onClick={close}><X size={20}/></button></header><div className="pilot-dialog-body"><div className="pilot-detail-actions"><a className="pilot-pdf" href={'/api/pilot/documents/'+detail.document_id+'/pdf'} target="_blank" rel="noreferrer"><FileText size={16}/>원본 PDF 열기<ArrowUpRight size={14}/></a><button className="secondary" aria-pressed={bookmarks.includes(detail.id)} onClick={()=>toggleBookmark(detail.id)}><Bookmark size={14}/>{bookmarks.includes(detail.id)?'북마크됨':'북마크'}</button></div><AbstractOverview paper={detail}/><div className="pilot-section-title"><h2>구조화한 주장 {detail.claims.length}개</h2><span>원문 구절 대조 · 사람 검토 대기</span></div>{detail.claims.map(c=><ClaimCard key={c.id} claim={c} documentId={detail.document_id}/>)}{!detail.claims.length&&<p className="pilot-empty">아직 이 논문의 주장·수치 분석을 수행하지 않았습니다.</p>}</div></>}</dialog>
 </div>;
}
function BookmarkRow({id,onOpen,onRemove}:{id:string;onOpen:(id:string)=>void;onRemove:(id:string)=>void}){
 const [paper,setPaper]=useState<Detail|null>(null);
 useEffect(()=>{const ctrl=new AbortController();get<Detail>('/papers/'+id,ctrl.signal).then(setPaper).catch(()=>{});return()=>ctrl.abort();},[id]);
 return <div className="pilot-bookmark-row"><Bookmark size={16}/><button onClick={()=>onOpen(id)}>{paper?.title||'저장한 논문 불러오는 중…'}</button><button aria-label="북마크 해제" onClick={()=>onRemove(id)}><X size={15}/></button></div>;
}

