import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, BookOpen, Bookmark, ChevronRight, CircleHelp, Clock3, FileText, Layers3, Lock, Menu, MessageSquare, Plus, Search, Settings2, Trash2, Undo2, Upload, Users, X } from 'lucide-react';
import { roleLabels } from './access';
import { CommonWikiRefresh, WikiRefresh } from './WikiRefresh';
import { ProcessingBadge, ProcessingView, useProcessing } from './ProcessingActivity';
import { TermDictionary } from './TermDictionary';
import { BookA } from 'lucide-react';
import { api, displayName } from './identity';
import type { Session } from './identity';
import { AccessDenied, LoginGate, ProfileCard, UserManagement } from './AccountViews';
import { get, personalKey, readHistory, readBookmarks, readView, time, viewHash, viewNames } from './pilotData';
import type { CorpusStatus, Detail, Expert, Paper, PilotView, ConversationTurn, ResearchRequest, ResearchRequestSummary, SavedSearch, Summary, Wiki } from './pilotData';
import { AbstractOverview, ClaimCard, GuideView, Pager, StatusView, WikiCard } from './PilotViews';
import { ExpertCreate } from './ExpertCreate';
import { ResearchDialogue } from './ResearchDialogue';
import { PaperKnowledge } from './PaperKnowledge';
import { ResearchComposer } from './ResearchComposer';
import { ReportPane } from './ReportPane';
import { useReports } from './useReports';
import { readReportStream, reportFocus } from './reportData';
import { contextRunIds, conversationTurns, saveConversation } from './researchConversation';
import { applyResearchRequest, detachConversationTurns, localOnlyQuestionCount, requestPending, requestTurn, unavailableRequestTurn, recentResearchEntries } from './researchRequests';
import { KnowledgePreparation, ResearchRequestHistory, useKnowledgeProgress, useResearchRequests } from './KnowledgeProgress';
import type { ProgressEvent } from './researchConversation';
import { researchModeLabels, savedResearchMode } from './researchAnswer';
import { boundedPage, reconcileExpertScope } from './workspaceState';
import { ExpertDelete } from './ExpertDelete';
import type { DeletedExpert } from './ExpertDelete';
import { CategoryChips, LibraryCategories } from './LibraryCategories';
import type { CategoryCounts, CategorySelection } from './LibraryCategories';
import './PilotApp.css';
import './PilotLayout.css';
const descriptions:Record<PilotView,string>={
 search:'질문을 이어가며 전문가의 Wiki와 논문에서 근거를 확인하세요. 앞선 대화의 맥락을 함께 참고합니다.',
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
 const [turns,setTurns]=useState<ConversationTurn[]>([]),[progress,setProgress]=useState<ProgressEvent[]>([]);
 const [localQuestionBusy,setQuestionBusy]=useState(false);
 const questionBusy=localQuestionBusy||turns.some(t=>t.status==='pending');
 const reports=useReports();
 const turnsRef=useRef<ConversationTurn[]>([]),conversationId=useRef<string|null>(null);
 const [visibility,setVisibility]=useState<'team'|'private'>('team'),[history,setHistory]=useState<SavedSearch[]>(()=>readHistory(session.user.employee_id)),[savedId,setSavedId]=useState<string|null>(null);
 const [personalFilter,setPersonalFilter]=useState<'all'|'private'|'trash'|'bookmarks'>('all'),[bookmarks,setBookmarks]=useState(()=>readBookmarks(session.user.employee_id));
 const dialog=useRef<HTMLDialogElement>(null),selection=useRef(0),searchEpoch=useRef(0);
 const searchController=useRef<AbortController|null>(null),viewRef=useRef(view);
 const stopRequested=useRef(new Set<string>()),sharing=useRef(new Set<string>()),requestOpenEpoch=useRef(0);
 const visibilityRef=useRef(visibility);visibilityRef.current=visibility;
 function updateTurns(next:ConversationTurn[]){turnsRef.current=next;setTurns(next);}
 function persistConversation(next:ConversationTurn[]){
  if(!next.length)return;
  const id=conversationId.current??crypto.randomUUID();conversationId.current=id;setSavedId(id);
  const record={...saveConversation(id,next,visibilityRef.current),reportId:reports.report?.id};
  setHistory(old=>[record,...old.filter(r=>r.id!==id)].slice(0,100));
 }
 function updateConversationTurn(chatId:string,turnId:string,change:(turn:ConversationTurn)=>ConversationTurn){
  if(conversationId.current===chatId&&turnsRef.current.some(t=>t.id===turnId)){
   const next=turnsRef.current.map(t=>t.id===turnId?change(t):t);
   if(next.every((t,i)=>t===turnsRef.current[i]))return;
   updateTurns(next);persistConversation(next);
  }else setHistory(old=>old.map(record=>{
   if(record.id!==chatId||!record.turns)return record;
   const next=record.turns.map(t=>t.id===turnId?change(t):t);
   if(next.every((t,i)=>t===record.turns![i]))return record;
   return {...record,...saveConversation(chatId,next,record.visibility),deleted:record.deleted};
  }));
 }
 function receiveRequest(value:ResearchRequest,chatId:string,turnId:string){updateConversationTurn(chatId,turnId,turn=>applyResearchRequest(turn,value));}
 const activeExperts=summary?.experts.filter(e=>selectedExperts.includes(e.id)&&e.enabled!==0)||[];
 const privateContext=contextRunIds(turns).length>0||!!(reports.open&&reportFocus(reports.report,reports.sectionId).report_id);
 const nextVisibility=privateContext?'private':visibility;
 const browsingExpert=summary?.experts.find(e=>e.id===expert);
 const needsReview=summary?.page_states.filter(s=>s.status!=='ready'&&s.status!=='excluded').reduce((n,s)=>n+s.count,0)||0;
 function cancelSearch(){
  searchEpoch.current++;searchController.current?.abort();searchController.current=null;setQuestionBusy(false);
  const next=detachConversationTurns(turnsRef.current);
  if(next.some((t,i)=>t!==turnsRef.current[i])){
   updateTurns(next);persistConversation(next);
  }
 }
 async function stopResearch(){
  const turn=turnsRef.current.find(t=>t.status==='pending'&&t.durable),chatId=conversationId.current;
  if(!turn){cancelSearch();return;}
  if(!turn.request){stopRequested.current.add(turn.id);setNotice('질문 등록을 마치는 대로 서버 작업을 중지합니다.');return;}
  if(turn.request.is_owner===false){setNotice('다른 사용자의 질문은 작성자만 중지할 수 있습니다.');return;}
  try{const value=await api<ResearchRequest>('/api/pilot/research-requests/'+encodeURIComponent(turn.request.id)+'/cancel',{method:'POST'});if(chatId)receiveRequest(value,chatId,turn.id);refreshData();}
  catch(e){setNotice(e instanceof Error?e.message:'작업을 중지하지 못했습니다.');}
 }
 function navigate(next:PilotView){cancelSearch();close();viewRef.current=next;setView(next);location.hash=viewHash(next);setMobile(false);setQuery('');setSubmitted('');setPage(0);setError('');setBusy(false);}
 function newQuestion(){navigate('search');reports.clear();conversationId.current=null;updateTurns([]);setProgress([]);setSavedId(null);setSelectedExperts([]);setExpert('');setSelectionMode('auto');}
 useEffect(()=>{const change=()=>{if(location.hash==='#main')return;const next=readView();if(next!==viewRef.current){cancelSearch();close();viewRef.current=next;setPage(0);setQuery('');setSubmitted('');}setView(next);setMobile(false);if(location.hash!==viewHash(next))window.history.replaceState(null,'',viewHash(next));};change();window.addEventListener('hashchange',change);return()=>{window.removeEventListener('hashchange',change);searchEpoch.current++;searchController.current?.abort();};},[]);
 function refreshData(){setDataRevision(value=>value+1);}
 const processing=useProcessing(refreshData);
 const serverHistory=useResearchRequests(dataRevision),knowledge=useKnowledgeProgress(dataRevision,view==='wiki'||view==='settings');
 const pendingKey=turns.filter(t=>t.status==='pending'&&t.request&&requestPending(t.request.status)).map(t=>t.request!.id).join(',');
 useEffect(()=>{
  if(view!=='search'||!pendingKey)return;
  const controller=new AbortController(),chatId=conversationId.current;let timer:ReturnType<typeof setTimeout>;
  async function poll(){
   try{if(!document.hidden)for(const id of pendingKey.split(',')){
    let value:ResearchRequest;
    try{value=await get<ResearchRequest>('/research-requests/'+encodeURIComponent(id),controller.signal);}
    catch(e){if(!controller.signal.aborted&&conversationId.current===chatId&&e&&typeof e==='object'&&'status' in e&&e.status===404){const turn=turnsRef.current.find(t=>t.request?.id===id);if(turn&&chatId)updateConversationTurn(chatId,turn.id,unavailableRequestTurn);continue;}throw e;}
    if(controller.signal.aborted||conversationId.current!==chatId)return;
    const turn=turnsRef.current.find(t=>t.request?.id===id);if(turn&&chatId)receiveRequest(value,chatId,turn.id);
    if(!requestPending(value.status))refreshData();
   }}catch(e){if(!controller.signal.aborted)setNotice(e instanceof Error?e.message:'처리 상태를 확인하지 못했습니다. 자동으로 다시 확인합니다.');}
   finally{if(!controller.signal.aborted)timer=setTimeout(poll,2500);}
  }
  void poll();return()=>{controller.abort();clearTimeout(timer);};
 },[view,pendingKey]);
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
  if(!question||!summary||questionBusy||reports.busy||searchController.current)return;
  const editing=reports.open&&reports.mode==='edit'&&!!reports.selected&&!!reports.report&&!reports.historical;
  const focus=reports.open?reportFocus(reports.report,reports.sectionId):{};
  if((!editing||reports.research)&&selectionMode==='manual'&&!activeExperts.length){setError('질문할 수 있는 전문가를 선택해 주세요.');return;}
  const previous=turnsRef.current,request=++searchEpoch.current,controller=new AbortController();
  const turn:ConversationTurn={id:crypto.randomUUID(),question,expertIds:selectionMode==='manual'?activeExperts.map(e=>e.id):[],expertNames:selectionMode==='manual'?activeExperts.map(e=>e.name):[],createdAt:new Date().toISOString(),status:'pending',durable:!editing,historyRunIds:contextRunIds(previous),focusTitle:focus.report_id?reports.selected?.title:undefined};
  searchController.current=controller;setQuestionBusy(true);setError('');setProgress([]);setQuery('');
  updateTurns([...previous,turn]);persistConversation(turnsRef.current);
  const chatId=conversationId.current!;
  function patchTurn(change:Partial<ConversationTurn>){updateTurns(turnsRef.current.map(t=>t.id===turn.id?{...t,...change}:t));}
  try{
   const onProgress=(event:ProgressEvent)=>{if(request===searchEpoch.current&&!controller.signal.aborted)setProgress(old=>old.at(-1)?.message===event.message?old:[...old,event].slice(-10));};
   if(editing&&reports.open){
    const response=await fetch('/api/pilot/reports/'+encodeURIComponent(reports.report!.id)+'/edit-stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:reports.report!.revision,section_id:reports.sectionId,instruction:question,run_ids:contextRunIds(previous),research:reports.research,expert_ids:turn.expertIds,selection:selectionMode}),signal:controller.signal});
    const result=await readReportStream(response,controller.signal,onProgress);
    if(request!==searchEpoch.current||controller.signal.aborted)return;
    reports.accept(result);
    patchTurn({status:'completed',expertIds:[],expertNames:[],reportChange:{reportId:result.id,revision:result.revision,sectionTitle:reports.selected!.title,model:result.model,elapsedMs:result.usage.elapsed_ms??0}});
   }else{
    // Registration continues across navigation so its server ID is never discarded.
    const value=await api<ResearchRequest>('/api/pilot/research-requests',{method:'POST',body:JSON.stringify({question,expert_ids:turn.expertIds,selection:selectionMode,visibility:nextVisibility,history_run_ids:contextRunIds(previous),...focus})});
    receiveRequest(value,chatId,turn.id);
    if(conversationId.current===chatId){setVisibility(value.visibility);visibilityRef.current=value.visibility;}
    if(stopRequested.current.delete(turn.id))try{receiveRequest(await api<ResearchRequest>('/api/pilot/research-requests/'+encodeURIComponent(value.id)+'/cancel',{method:'POST'}),chatId,turn.id);}catch(e){setNotice(e instanceof Error?e.message:'질문은 등록되었으나 중지하지 못했습니다. 내 질문에서 다시 중지할 수 있습니다.');}
    refreshData();return;
   }
   persistConversation(turnsRef.current);
  }catch(e){
   if(!editing){stopRequested.current.delete(turn.id);updateConversationTurn(chatId,turn.id,t=>({...t,status:'error',message:(e instanceof Error?e.message:'질문을 등록하지 못했습니다.')+' 서버의 내 질문 목록에서 등록 여부를 확인해 주세요.'}));refreshData();return;}
   if(request===searchEpoch.current&&!controller.signal.aborted){
    patchTurn({status:'error',message:e instanceof Error?e.message:'답변을 준비하지 못했습니다.'});persistConversation(turnsRef.current);
   }
  }finally{if(request===searchEpoch.current){setQuestionBusy(false);searchController.current=null;}}
 }
 function openHistory(r:SavedSearch){
  navigate('search');conversationId.current=r.id;
  const reopened=conversationTurns(r),focus=[...reopened].reverse().find(t=>t.request?.is_owner!==false&&t.request?.report_id)?.request,reportId=focus?.report_id??r.reportId;
  if(reportId)void reports.load(reportId,focus?.report_revision??undefined)?.then(()=>{if(conversationId.current===r.id&&focus?.report_section_id)reports.select(focus.report_section_id);});else reports.clear();
  updateTurns(reopened.map(t=>t.request?.is_owner===false?{...t,result:undefined,status:'error',message:'현재 공유 상태를 확인하고 있습니다.'}:t));setProgress([]);setSavedId(r.id);setVisibility(r.visibility);visibilityRef.current=r.visibility;
  setSelectedExperts(summary?reconcileExpertScope(summary.experts,r.expertIds,'').selectedIds:[]);
  const epoch=searchEpoch.current;
  // Completed entries also refresh: an owner may have withdrawn a shared question.
  for(const turn of reopened)if(turn.request)void get<ResearchRequest>('/research-requests/'+encodeURIComponent(turn.request.id)).then(value=>{if(epoch===searchEpoch.current&&conversationId.current===r.id)receiveRequest(value,r.id,turn.id);}).catch(()=>{
   if(epoch===searchEpoch.current&&conversationId.current===r.id&&turn.request?.is_owner===false)updateConversationTurn(r.id,turn.id,unavailableRequestTurn);
  });
 }
 async function openRequest(item:ResearchRequestSummary){
  const epoch=searchEpoch.current,opening=++requestOpenEpoch.current;
  let value:ResearchRequest;
  try{value=await get<ResearchRequest>('/research-requests/'+encodeURIComponent(item.id));}
  catch(e){if(epoch===searchEpoch.current&&opening===requestOpenEpoch.current)setNotice(e instanceof Error?e.message:'질문을 불러오지 못했습니다.');return;}
  if(epoch!==searchEpoch.current||opening!==requestOpenEpoch.current)return;
  const existing=history.find(r=>!r.deleted&&r.turns?.some(t=>t.request?.id===value.id));
  if(existing){openHistory(existing);const turn=existing.turns!.find(t=>t.request?.id===value.id)!;receiveRequest(value,existing.id,turn.id);return;}
  newQuestion();const id='request-'+value.id;conversationId.current=id;setSavedId(id);updateTurns([requestTurn(value)]);setVisibility(value.visibility);visibilityRef.current=value.visibility;
  if(value.is_owner!==false){
   if(value.report_id)void reports.load(value.report_id,value.report_revision??undefined)?.then(()=>{if(conversationId.current===id&&value.report_section_id)reports.select(value.report_section_id);});
   persistConversation(turnsRef.current);
  }
  setSelectedExperts(summary?reconcileExpertScope(summary.experts,value.expert_ids,'').selectedIds:[]);
 }
 function changeRecord(id:string,change:Partial<SavedSearch>){setHistory(old=>old.map(r=>r.id===id?{...r,...change}:r));}
 function changeVisibility(next:'team'|'private'){visibilityRef.current=next;setVisibility(next);}
 async function shareRequest(turn:ConversationTurn){
  if(!turn.request||turn.request.is_owner===false||sharing.current.has(turn.request.id))return;
  const chatId=conversationId.current,id=turn.request.id;sharing.current.add(id);
  try{const value=await api<ResearchRequest>('/api/pilot/research-requests/'+encodeURIComponent(id)+'/visibility',{method:'PATCH',body:JSON.stringify({visibility:turn.request.visibility==='team'?'private':'team'})});if(chatId)receiveRequest(value,chatId,turn.id);refreshData();}
  catch(e){setNotice(e instanceof Error?e.message:'공개 범위를 변경하지 못했습니다.');}
  finally{sharing.current.delete(id);}
 }
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
   <button className="pilot-new" onClick={newQuestion}><Plus size={17}/>새 대화 시작<ArrowUpRight size={15}/></button>
   <div className="pilot-nav-label">RESEARCH</div>
   <nav aria-label="주 메뉴">{([['search',MessageSquare],['wiki',BookOpen],['library',FileText],['dictionary',BookA],['team',Users]] as const).map(([id,Icon])=><button key={id} aria-current={view===id?'page':undefined} onClick={()=>navigate(id)}><Icon size={18}/>{viewNames[id]}{id==='library'&&<small>{summary?.counts.papers??'—'}</small>}</button>)}</nav>
   <div className="pilot-recent-title"><span>내 최근 대화</span><Clock3 size={13}/></div><div className="pilot-recent">{recentResearchEntries(history,serverHistory.items).map(entry=>entry.kind==='conversation'?<button key={'chat-'+entry.record.id} onClick={()=>openHistory(entry.record)}><MessageSquare size={13}/><span>{entry.record.question}</span></button>:<button key={'request-'+entry.request.id} onClick={()=>openRequest(entry.request)}><MessageSquare size={13}/><span>{entry.request.question}</span></button>)}{!history.some(r=>!r.deleted)&&!serverHistory.items.length&&<p>이전 대화를 열어 질문을 이어갈 수 있습니다.</p>}</div>
   <div className="pilot-side-bottom"><ProcessingBadge data={processing.data} error={processing.error} reviewPages={needsReview} onOpen={()=>navigate('status')}/>
    {isExpertManager&&<button onClick={()=>navigate('settings')} aria-current={view==='settings'?'page':undefined}><Settings2 size={17}/>전문가 설정</button>}
    {session.user.permissions.manage_users&&<button onClick={()=>navigate('users')} aria-current={view==='users'?'page':undefined}><Users size={17}/>유저 관리</button>}
    <button onClick={()=>navigate('guide')}><CircleHelp size={17}/>사용 가이드</button>
    <button className="pilot-profile" onClick={()=>navigate('personal')} aria-current={view==='personal'?'page':undefined}><span className="user-avatar">{displayName(session).slice(0,1)}</span><span>{displayName(session)}<small>{roleLabels[session.user.role]} · 내 페이지</small></span><ChevronRight size={15}/></button>
   </div>
  </aside>
  <div className="pilot-workspace"><header className="pilot-top"><button className="pilot-menu-toggle" aria-label="메뉴 열기" onClick={()=>setMobile(true)}><Menu size={21}/></button><strong>{viewNames[view]}</strong><button className="account-session" onClick={()=>navigate('personal')}><span>{displayName(session)}</span><span className={'account-role '+session.user.role}>{roleLabels[session.user.role]}</span>{session.mode==='local_admin'&&<small>임시 접속</small>}</button></header>
  <main id="main" tabIndex={-1} className={'pilot-main'+(view==='search'?' research-main':'')}>
   {notice&&<div className="pilot-notice" role="status">{notice}</div>}{error&&<div className="pilot-error" role="alert">{error}<button onClick={()=>window.location.reload()}>다시 시도</button></div>}
   {view==='settings'&&isExpertManager&&lastDeleted&&<div className="expert-delete-notice" role="status"><span>{lastDeleted.name} 전문가를 삭제했습니다.</span><button disabled={restoring} onClick={undoExpertDeletion}>{restoring?'복원 중…':'실행 취소'}</button><button disabled={restoring} aria-label="삭제 알림 닫기" onClick={()=>setLastDeleted(null)}><X size={15}/></button></div>}
   <div className="pilot-page-heading"><div className="pilot-hero"><div className="eyebrow">EVIDENCE STUDIO</div><h1>{view==='search'?'전문가와 연구 대화를 시작하세요.':viewNames[view]}</h1><p>{descriptions[view]}</p></div>
    {view==='library'&&<div className="pilot-header-actions"><button className="secondary" disabled={busy} onClick={refreshData}>새로고침</button><button className="primary" disabled title="논문 업로드 기능을 준비 중입니다."><Upload size={15}/>논문 추가</button></div>}
    {(view==='team'||view==='personal')&&<button className="primary" onClick={newQuestion}><Plus size={15}/>새 질문</button>}
   </div>
   {(view==='library'||view==='status')&&<div className="pilot-compact-stats"><span><strong>{summary?.curated_papers??'—'}</strong>편 구조화</span><span><strong>{summary?.counts.claims??'—'}</strong>개 주장</span><span><strong>{summary?.counts.measurements??'—'}</strong>개 수치</span><span><strong>{summary?.counts.condition_values??'—'}</strong>개 조건 항목</span></div>}

   {view==='search'&&<>
    <div className="report-workspace-tools"><div className="report-mobile-tabs" role="group" aria-label="작업 화면"><button aria-pressed={reports.mobilePane==='chat'} onClick={()=>reports.setMobilePane('chat')}>AI 대화</button>{reports.open&&<button aria-pressed={reports.mobilePane==='report'} onClick={()=>reports.setMobilePane('report')}>보고서</button>}</div><button className="secondary" disabled={questionBusy||reports.busy} onClick={()=>{reports.setOpen(!reports.open);if(!reports.open)reports.setMobilePane('report');}}><FileText size={15}/>{reports.open?'보고서 접기':'보고서 열기'}</button></div>
    <div className={'report-workspace '+(reports.open?'with-report':'')+' mobile-'+reports.mobilePane}>
    <section className="research-chat" aria-label="전문가와 대화">
    {record&&<div className="pilot-result-actions"><span>이 브라우저의 대화 {turns.length}회 · 공개 범위는 각 질문에 표시됩니다.</span><button disabled={questionBusy} onClick={()=>{changeRecord(record.id,{deleted:true});conversationId.current=null;updateTurns([]);setSavedId(null);navigate('personal');setPersonalFilter('trash');}}><Trash2 size={13}/>대화 삭제</button></div>}
    <ResearchDialogue turns={turns} progress={progress} busy={questionBusy} onPaper={openPaper} onRetry={text=>{setQuery(text);document.getElementById('research-question')?.focus();}} onNotice={setNotice} onShare={turn=>void shareRequest(turn)} onChanged={refreshData}/>
    <div className="research-composer">
    {reports.open&&reports.selected&&!reports.historical&&<div className="report-focus"><div><strong>{reports.selected.title}</strong><span>이 구간에 집중</span><button disabled={questionBusy} onClick={()=>reports.select('')}>선택 해제</button></div><div role="group" aria-label="선택 구간 작업"><button disabled={questionBusy} aria-pressed={reports.mode==='question'} onClick={()=>reports.setMode('question')}>구간 질문</button><button disabled={questionBusy} aria-pressed={reports.mode==='edit'} onClick={()=>reports.setMode('edit')}>구간 수정</button></div>{reports.mode==='edit'?<label className="report-research-toggle"><input type="checkbox" checked={reports.research} disabled={questionBusy} onChange={e=>reports.setResearch(e.target.checked)}/>추가 논문 조사 <span>끄면 현재 구간·앞선 답변의 근거 재사용</span></label>:<small>답변만 제공하며 보고서는 변경하지 않습니다.</small>}</div>}
    <ResearchComposer editing={reports.open&&reports.mode==='edit'&&!!reports.selected&&!reports.historical} focusTitle={reports.open&&!reports.historical?reports.selected?.title:undefined} value={query} onChange={setQuery} onSend={()=>void askQuestion()} onStop={()=>void stopResearch()} busy={questionBusy} canSend={!!summary&&!reports.busy&&((reports.open&&reports.mode==='edit'&&!reports.research)||(selectionMode!=='manual'||!!activeExperts.length))} hasTurns={!!turns.length}/>
    <div className="pilot-question-options"><div className="pilot-segments" aria-label="전문가 선택 방식"><button disabled={questionBusy} aria-pressed={selectionMode==='auto'} onClick={()=>setSelectionMode('auto')}>전문가 자동 선택</button><button disabled={questionBusy} aria-pressed={selectionMode==='manual'} onClick={()=>setSelectionMode('manual')}>직접 선택</button></div><div className="pilot-segments" aria-label="새 질문 공개 범위"><button disabled={questionBusy||privateContext} aria-pressed={nextVisibility==='team'} onClick={()=>changeVisibility('team')}><Users size={13}/>팀에 공유</button><button disabled={questionBusy||privateContext} aria-pressed={nextVisibility==='private'} onClick={()=>changeVisibility('private')}><Lock size={13}/>나만 보기</button></div></div>
    {privateContext&&<p className="pilot-muted">앞선 개인 대화·보고서 맥락을 포함하는 질문은 나만 보기로 저장됩니다.</p>}
    <p className="research-context-note">{turns.length?'앞선 대화를 참고하되, 각 답변의 근거는 전문가의 Wiki와 논문에서 다시 확인합니다.':'전문가의 Wiki와 담당 논문에서 질문에 맞는 근거를 찾고 대화를 이어갑니다.'}</p>
    {selectionMode==='manual'&&<div className="pilot-expert-choices">{summary?.experts.filter(e=>e.enabled!==0).map(e=><label key={e.id}><input type="checkbox" disabled={questionBusy} checked={selectedExperts.includes(e.id)} onChange={()=>setSelectedExperts(old=>old.includes(e.id)?old.filter(x=>x!==e.id):[...old,e.id])}/><span>{e.name}<small>논문 {e.paper_count}편</small></span></label>)}</div>}
    {!turns.length&&!questionBusy&&<div className="pilot-prompts">{['OCTRAM retention','FeFET endurance','CFET SRAM','NAND erase'].map(q=><button key={q} disabled={!summary} onClick={()=>askQuestion(q)}>{q}<ArrowUpRight size={14}/></button>)}</div>}
    <p className="pilot-muted">새 질문에 선택한 공개 범위가 적용됩니다. 질문·답변은 서버에 저장되며 대화 묶음과 보고서 연결은 이 브라우저에 보관됩니다.</p>
    </div>
   </section>
    {reports.open&&<ReportPane state={reports} locked={questionBusy} onPaper={openPaper}/>}
    </div></>}

   {view==='library'&&<div className="pilot-library-layout">
    <LibraryCategories value={categories} counts={library.facet_counts} total={library.total} onChange={changeCategories}/><div className="pilot-library-results">
    <CategoryChips value={categories} onChange={changeCategories}/><div className="pilot-list-tools"><form className="pilot-search" onSubmit={e=>{e.preventDefault();setSubmitted(query);setPage(0);}}><Search size={18}/><input aria-label="논문 찾기" value={query} onChange={e=>setQuery(e.target.value)} placeholder="제목 또는 분야 검색"/><button type="submit">검색</button></form><select aria-label="논문 전문가 범위" value={expert} onChange={e=>{setExpert(e.target.value);setPage(0);}}><option value="">모든 전문가</option>{summary?.experts.map(e=><option key={e.id} value={e.id}>{e.name}{e.enabled===0?' · 사용 중지':''}</option>)}</select><button className="secondary" onClick={exportPage}>현재 목록 내보내기</button></div>
    {browsingExpert&&<div className="pilot-scope-context"><span>{browsingExpert.name}의 담당 논문{browsingExpert.enabled===0?' · 사용 중지된 전문가의 자료 열람':''}</span><button className="pilot-inline-link" onClick={()=>showExpert(browsingExpert)}>전문가 지식 보기</button><button className="pilot-inline-link" onClick={()=>{setExpert('');setPage(0);}}>전문가 필터 해제</button></div>}
    {busy?<p role="status" className="pilot-muted">논문을 불러오고 있습니다…</p>:<><div className="pilot-section-title"><h2>논문 {library.total}편</h2><span>8편씩 표시</span></div><div className="pilot-paper-grid">{library.papers.map(p=><article className="pilot-paper" key={p.id}><div className="pilot-labels">{p.topics.slice(0,3).map(t=><span title={t.basis} key={t.tag}>{t.tag}</span>)}{p.status!=='ready'&&<em>일부 페이지 확인 필요</em>}<button className="pilot-bookmark" aria-label={p.title+' 북마크'} aria-pressed={bookmarks.includes(p.id)} onClick={()=>toggleBookmark(p.id)}><Bookmark size={16} fill={bookmarks.includes(p.id)?'currentColor':'none'}/></button></div><button className="pilot-paper-title" onClick={()=>openPaper(p.id)}><h3>{p.title}</h3></button>{p.title_source==='unresolved'&&<small>제목 확인 필요 · 파일명으로 표시</small>}<AbstractOverview paper={p}/><footer><span>{p.page_count} pages</span><strong>{p.claim_count?'주장 '+p.claim_count+'개 정리':'구조화된 주장 없음'}</strong><button aria-label={p.title+' 상세 보기'} onClick={()=>openPaper(p.id)}><ArrowUpRight size={16}/></button></footer></article>)}</div>{!library.total&&<p className="pilot-empty">일치하는 논문이 없습니다.</p>}<Pager page={page} total={library.total} size={8} onPage={setPage} label="논문"/></>}
   </div></div>}

   {view==='wiki'&&<>
    <div className="pilot-experts-overview">{summary?.experts.map(e=><button key={e.id} aria-pressed={expert===e.id} onClick={()=>{setExpert(expert===e.id?'':e.id);setPage(0);setSubmitted('');setQuery('');}}><BookOpen size={18}/><strong>{e.name}{e.enabled===0?' · 사용 중지':''}</strong><span>논문 {e.paper_count} · 원천 준비 {knowledge.items.find(p=>p.id===e.id)?.coverage.ready??'—'}편</span></button>)}</div>
    {browsingExpert&&<section className="pilot-expert-context" aria-label="선택한 전문가"><h2>{browsingExpert.name}</h2><p>{browsingExpert.overview}</p>{browsingExpert.enabled===0&&<p className="pilot-muted">사용 중지된 전문가는 질의와 Wiki 검색에서 제외됩니다. 담당 논문은 계속 열람할 수 있습니다.</p>}<div><button className="secondary" onClick={()=>showExpertPapers(browsingExpert)}>담당 논문 {browsingExpert.paper_count}편 보기</button><button className="primary" disabled={browsingExpert.enabled===0} onClick={()=>askExpert(browsingExpert)}>이 전문가에게 질문</button>{isExpertManager&&<button className="secondary" onClick={()=>setExpertEditor({id:browsingExpert.id})}><Settings2 size={14}/>설정 확인·수정</button>}</div></section>}
    {knowledge.error&&<p className="pilot-muted" role="status">{knowledge.error}</p>}
    {browsingExpert&&browsingExpert.enabled!==0&&<KnowledgePreparation expert={browsingExpert} progress={knowledge.items.find(p=>p.id===browsingExpert.id)} onChanged={refreshData} onAsk={question=>{askExpert(browsingExpert);setQuery(question);}}/>}
    <div className="pilot-list-tools"><form className="pilot-search" onSubmit={e=>{e.preventDefault();setSubmitted(query);setPage(0);}}><Search size={18}/><input aria-label="Wiki 검색" value={query} onChange={e=>setQuery(e.target.value)} placeholder="알고 싶은 사실이나 전문 용어 검색"/><button type="submit">검색</button></form>{expert&&<button className="secondary" onClick={()=>{setExpert('');setPage(0);}}>전문가 필터 해제<X size={13}/></button>}</div>
    <div className="pilot-section-title"><h2>{browsingExpert?.name||'전체 전문가'}의 Wiki</h2><span>{wikis.total}개 문서 · 6개씩 표시</span><button className="secondary" onClick={refreshData} disabled={busy}>새로고침</button></div>
    <p className="pilot-muted">지식 반영 이력은 <button className="pilot-inline-link" onClick={()=>navigate('status')}>자료 현황</button>에서 확인할 수 있습니다.</p>
    {busy?<p role="status" className="pilot-muted">Wiki를 불러오고 있습니다…</p>:<>{wikis.wikis.map(w=><WikiCard key={w.id} wiki={w} refreshKey={dataRevision} onPaper={openPaper}/>)}{!wikis.total&&<p className="pilot-empty">해당 조건의 Wiki가 없습니다.</p>}<Pager page={page} total={wikis.total} size={6} onPage={setPage} label="Wiki"/></>}
   </>}

   {(view==='team'||view==='personal')&&<>
    {view==='personal'&&<ProfileCard session={session} questions={serverHistory.total+localOnlyQuestionCount(history)} bookmarks={bookmarks.length}/>}
    <p className="pilot-local-note">질문과 답변은 서버에 저장됩니다. 팀에 공유한 질문만 다른 사용자가 볼 수 있습니다. 대화 묶음 100개·북마크·휴지통은 이 브라우저에서 관리합니다.</p>
    {view==='personal'&&<div className="pilot-segments">{([['all','내 대화'],['private','비공개'],['trash','휴지통'],['bookmarks','북마크 '+bookmarks.length]] as const).map(([id,label])=><button key={id} aria-pressed={personalFilter===id} onClick={()=>setPersonalFilter(id)}>{label}</button>)}</div>}
    {(view==='team'||!['trash','bookmarks'].includes(personalFilter))&&<ResearchRequestHistory team={view==='team'} privateOnly={view==='personal'&&personalFilter==='private'} revision={dataRevision} requests={view==='personal'?serverHistory.items:undefined} loadError={view==='personal'?serverHistory.error:''} onOpen={openRequest}/>}
    {view==='personal'&&(personalFilter==='bookmarks'?<div className="pilot-bookmarks">{bookmarks.map(id=><BookmarkRow key={id} id={id} onOpen={openPaper} onRemove={toggleBookmark}/>)}{!bookmarks.length&&<p className="pilot-empty">저장한 논문이 없습니다. 라이브러리에서 북마크를 눌러보세요.</p>}</div>:<><h2 className="pilot-local-heading">이 브라우저의 대화 묶음</h2>{visibleHistory.map(r=><article className="pilot-history" key={r.id}><button className="pilot-history-title" onClick={()=>openHistory(r)} disabled={r.deleted}>{r.question}<ArrowUpRight size={15}/></button><p>{r.expertNames.join(' · ')}</p><small>{time(r.createdAt)} · 대화 {r.turns?.length||1}회 · {researchModeLabels[savedResearchMode(r)]} 질의 · 주장 {r.result.claims.length}개 · 브라우저 대화 묶음</small><div><button onClick={()=>changeRecord(r.id,{deleted:!r.deleted})}>{r.deleted?<Undo2 size={13}/>:<Trash2 size={13}/>} {r.deleted?'복구':'삭제'}</button></div></article>)}{!visibleHistory.length&&<p className="pilot-empty">브라우저에 저장된 대화 묶음이 없습니다.</p>}</>)}
   </>}
   {view==='status'&&<><ProcessingView data={processing.data} error={processing.error} onPaper={openPaper} canRetryWiki={session.user.permissions.refresh_wiki} onRetried={processing.refresh}/><StatusView status={status} onPaper={openPaper} onChanged={refreshData}/></>}
   {view==='settings'&&(isExpertManager?<>{session.user.permissions.refresh_wiki&&<CommonWikiRefresh onCompleted={refreshData}/>}<div className="expert-settings-heading"><p>전문가의 설정을 확인·수정하거나 새로운 전문가를 추가하세요.</p><button className="primary" onClick={()=>setExpertEditor({})}><Plus size={16}/>전문가 추가</button></div><div className="pilot-settings-grid">{summary?.experts.map(e=><article key={e.id}><header><BookOpen size={17}/><h2>{e.name}</h2>{e.enabled===0&&<span className="expert-inactive">사용 안 함</span>}<button className="expert-card-delete" aria-label={e.name+' 삭제'} title="전문가 삭제" onClick={()=>setDeleteTarget(e)}><Trash2 size={13}/><span>삭제</span></button>{session.user.permissions.refresh_wiki&&<WikiRefresh expertId={e.id} expertName={e.name} disabled={e.enabled===0} onCompleted={refreshData}/>}</header><p title={e.overview}>{e.overview}</p><div className="expert-card-counts"><span>논문 <strong>{e.paper_count}</strong></span><span>주장 <strong>{e.claim_count}</strong></span><span>원천 준비 <strong>{knowledge.items.find(p=>p.id===e.id)?.coverage.ready??'—'}편</strong></span></div><footer><button className="secondary" aria-label={e.name+' 설정 확인·수정'} onClick={()=>setExpertEditor({id:e.id})}><Settings2 size={14}/>설정 확인·수정</button><button className="expert-knowledge-link" onClick={()=>showExpert(e)}>전문가 지식<ArrowUpRight size={14}/></button></footer></article>)}</div></>:<div className="pilot-empty"><Lock size={23}/><h2>관리자와 슈퍼 유저가 접근할 수 있습니다.</h2><button onClick={()=>navigate('wiki')}>전문가 지식으로 돌아가기</button></div>)}
   {view==='users'&&(session.user.permissions.manage_users?<UserManagement session={session}/>:<AccessDenied/>)}
   {view==='dictionary'&&<TermDictionary session={session}/>}
   {view==='guide'&&<GuideView/>}
  </main></div>
  {expertEditor&&isExpertManager&&<ExpertCreate key={expertEditor.id||'new'} expertId={expertEditor.id} onClose={()=>setExpertEditor(null)} onSaved={savedExpert}/>}
  {deleteTarget&&isExpertManager&&<ExpertDelete expert={deleteTarget} onClose={()=>setDeleteTarget(null)} onDeleted={deletedExpert}/>}
  <dialog ref={dialog} className="pilot-dialog" aria-label="논문 상세" onCancel={close} onClick={e=>{if(e.target===dialog.current)close();}}>{detail&&<><header><div><span>논문 · {detail.page_count} pages</span><h2>{detail.title}</h2></div><button className="icon-button" aria-label="논문 닫기" onClick={close}><X size={20}/></button></header><div className="pilot-dialog-body"><div className="pilot-detail-actions"><a className="pilot-pdf" href={'/api/pilot/documents/'+detail.document_id+'/pdf'} target="_blank" rel="noreferrer"><FileText size={16}/>원본 PDF 열기<ArrowUpRight size={14}/></a><button className="secondary" aria-pressed={bookmarks.includes(detail.id)} onClick={()=>toggleBookmark(detail.id)}><Bookmark size={14}/>{bookmarks.includes(detail.id)?'북마크됨':'북마크'}</button></div><AbstractOverview paper={detail}/><PaperKnowledge knowledge={detail.knowledge} revision={dataRevision} onPaper={openPaper} onExpert={id=>{navigate('wiki');setExpert(id);}}/><div className="pilot-section-title"><h2>구조화한 주장 {detail.claims.length}개</h2><span>원문 구절 대조 · 사람 검토 대기</span></div>{detail.claims.map(c=><ClaimCard key={c.id} claim={c} documentId={detail.document_id}/>)}{!detail.claims.length&&<p className="pilot-empty">별도의 구조화된 주장·수치 기록은 아직 없습니다. 원문 분석 상태와 공통 Wiki에서 준비된 근거를 확인하세요.</p>}</div></>}</dialog>
 </div>;
}
function BookmarkRow({id,onOpen,onRemove}:{id:string;onOpen:(id:string)=>void;onRemove:(id:string)=>void}){
 const [paper,setPaper]=useState<Detail|null>(null);
 useEffect(()=>{const ctrl=new AbortController();get<Detail>('/papers/'+id,ctrl.signal).then(setPaper).catch(()=>{});return()=>ctrl.abort();},[id]);
 return <div className="pilot-bookmark-row"><Bookmark size={16}/><button onClick={()=>onOpen(id)}>{paper?.title||'저장한 논문 불러오는 중…'}</button><button aria-label="북마크 해제" onClick={()=>onRemove(id)}><X size={15}/></button></div>;
}

