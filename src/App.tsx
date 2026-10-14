import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Upload, ArrowDownToLine, ArrowRight, ArrowUp, BookOpen, Check, CheckCheck, ChevronRight, CircleHelp, Clock3, Copy, FileSearch, FileText, FolderSync, Layers3, Library, Menu, MessageSquare, Plus, Search, Settings2, ShieldCheck, Sparkles, X, AlertCircle, RotateCw, Bookmark, SlidersHorizontal, Quote, ArrowUpRight, Users, UserRound, Lock, Share2, Trash2, Undo2 } from 'lucide-react';
import { incoming, suggestions, members, exampleConversations } from './data';
import { loadLibrary } from './api';
import { readExperts, selectExperts, buildContributions } from './experts';
import { ExpertKnowledge } from './ExpertKnowledge';
import { reviewWikiQuestions, sharedQuestionCount, syncExpertWikis } from './wiki';
import type { WikiPage } from './wiki';
import { ExpertRecommendation, ExpertContributions } from './ExpertJourney';
import { previewMembership, knowledgeDate } from './knowledge';
import { PaperEvidence } from './PaperEvidence';
import type { PaperTab } from './PaperEvidence';
import { AnswerEvidence } from './AnswerEvidence';
import { answerPlainText, captureAnswerEvidence, selectedStatement } from './answerSelection';
import { normalizeSearchText } from './terminology';
import { ExpertSettings } from './ExpertSettings';
import { QuestionOptions } from './QuestionOptions';
import { PaperUpload } from './PaperUpload';
import { canManageExperts, resolveDemoRole, roleLabels } from './access';
import type { Answer, ExpertCategory, ExpertSelection, Paper, View } from './types';

const currentRole = resolveDemoRole(import.meta.env.VITE_DEMO_ROLE);
const isAdmin = canManageExperts(currentRole);
const views: Record<View, string> = { ask:'연구 질문', library:'논문 라이브러리', experts:'전문가 지식', ingest:'자료 현황', team:'팀의 질문', personal:'내 페이지', settings:'전문가 설정' };
const navIcons = { ask:MessageSquare, library:Library, experts:BookOpen, ingest:FolderSync, team:Users, personal:UserRound, settings:Settings2 };
const memberName = (id:string) => members.find(member=>member.id===id)?.name || '팀원';
const uploaderLabel = (id:string) => id==='me'?'내가':`${memberName(id)}님이`;
function readView(): View { const value = location.hash.slice(1); return value in views ? value as View : 'ask'; }
function Brand({ small=false }: {small?:boolean}) { return <div className={`brand ${small?'small':''}`}><span className="brand-symbol"><Layers3 size={small?19:22}/></span>{!small && <span className="brand-name">Evidence <span>Studio</span></span>}</div>; }
function Tag({ children, tone='neutral' }: {children:React.ReactNode;tone?:string}) { return <span className={`tag ${tone}`}>{children}</span>; }

export default function App() {
  const [view,setView] = useState<View>(readView);
  const [mobile,setMobile] = useState(false);
  const [categories,setCategories] = useState<ExpertCategory[]>(readExperts);
  const [knowledgeExpertId,setKnowledgeExpertId] = useState('dram');
  const [wikis,setWikis] = useState<WikiPage[]>([]);
  const [wikiPhase,setWikiPhase] = useState<'idle'|'checking'|'updating'|'done'>('idle');
  const [proposal,setProposal] = useState<{text:string;mode:Answer['mode'];ids:string[];selection:ExpertSelection}|null>(null);
  const [library,setLibrary] = useState<Paper[]>([]);
  const [loaded,setLoaded] = useState(false);
  const [loadError,setLoadError] = useState('');
  const [selected,setSelected] = useState<string[]>([]);
  const [bookmarks,setBookmarks] = useState<string[]>([]);
  const [search,setSearch] = useState('');
  const [topic,setTopic] = useState('전체 주제');
  const [year,setYear] = useState('전체 연도');
  const [onlySaved,setOnlySaved] = useState(false);
  const [question,setQuestion] = useState('');
  const [scope,setScope] = useState('all');
  const [expertMode,setExpertMode] = useState<ExpertSelection>('auto');
  const [selectedExpertIds,setSelectedExpertIds] = useState<string[]>([]);
  const [answer,setAnswer] = useState<Answer|null>(null);
  const [history,setHistory] = useState<Answer[]>(exampleConversations);
  const [visibility,setVisibility] = useState<Answer['visibility']>('team');
  const [thinking,setThinking] = useState(false);
  const [activePaper,setActivePaper] = useState<Paper|null>(null);
  const [paperTab,setPaperTab] = useState<PaperTab>('overview');
  const [dialogKind,setDialogKind] = useState<'paper'|'review'|'guide'|'members'|'upload'|null>(null);
  const [scanning,setScanning] = useState(false);
  const [scanDone,setScanDone] = useState(false);
  const [reviewRetry,setReviewRetry] = useState(false);
  const [toast,setToast] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const timeouts = useRef<ReturnType<typeof setTimeout>[]>([]);
  const answerRef = useRef<HTMLHeadingElement>(null);
  const evidence = answer ? answer.paperIds.flatMap(id=>library.find(p=>p.id===id)||[]) : [];
  const filtered = library.filter(p=>(!onlySaved || bookmarks.includes(p.id)) && (topic==='전체 주제'||p.topic===topic) && (year==='전체 연도'||String(p.year)===year) && normalizeSearchText(`${p.title} ${p.authors} ${p.topic} ${p.filename} ${p.abstract}`).includes(normalizeSearchText(search)));

  const readyLibrary=library.filter(p=>p.status!=='pending');
  const selectable=filtered.filter(p=>p.status!=='pending');
  const pendingUploads=library.filter(p=>p.status==='pending');
  const addPapers=(files:File[])=>{
    const additions:Paper[]=files.map(file=>({id:crypto.randomUUID(),title:file.name.replace(/\.pdf$/i,''),filename:file.name,authors:'서지정보 확인 중',year:0,journal:'',topic:'미분류',pages:0,chunks:0,abstract:'',evidence:'',status:'pending',uploadedBy:'me'}));
    setLibrary(previous=>[...additions,...previous]);setSearch('');setTopic('전체 주제');setYear('전체 연도');setOnlySaved(false);setDialogKind(null);
    notify(`논문 ${additions.length}편을 준비 중 목록에 추가했습니다. · 업로드 화면 예시`);
  };
  const selectedExperts=categories.filter(category=>category.enabled&&selectedExpertIds.includes(category.id));
  const manualSelectionMissing=expertMode==='manual'&&!selectedExperts.length;
  const hasPaperScope=scope==='selected'&&selected.length>0;

  const notify = (message:string) => setToast(message);
  const later = (callback:()=>void,delay:number) => { timeouts.current.push(setTimeout(callback,delay)); };
  const navigate = (next:View) => { setView(next); location.hash=next; setMobile(false); };
  const toggleSelected = (id:string) => setSelected(prev=>prev.includes(id)?prev.filter(x=>x!==id):[...prev,id]);
  const showPaper = (paper:Paper,tab:PaperTab='overview') => { setActivePaper(paper); setPaperTab(tab); setDialogKind('paper'); };
  const resetConversation = () => { if(thinking) return; setAnswer(null); setProposal(null); setQuestion(''); navigate('ask'); };
  const init = () => { setLoadError(''); setLoaded(false); loadLibrary().then(result=>{setLibrary(result.papers);setLoaded(true);}).catch(error=>setLoadError(String(error.message))); };
  useEffect(()=>{ init(); return ()=>timeouts.current.forEach(clearTimeout); },[]);
  useEffect(()=>{ const change=()=>{if(location.hash!=='#main')setView(readView());}; window.addEventListener('hashchange',change); return ()=>window.removeEventListener('hashchange',change); },[]);
  useEffect(()=>{ const key=(e:KeyboardEvent)=>{if((e.ctrlKey||e.metaKey)&&e.key==='k'){e.preventDefault();navigate('library');later(()=>document.getElementById('paper-search')?.focus(),50);}};window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);},[]);
  useEffect(()=>{ if(!toast)return;const timer=setTimeout(()=>setToast(''),4200);return()=>clearTimeout(timer); },[toast]);
  useEffect(()=>{ if(dialogKind && !dialog.current?.open)dialog.current?.showModal();else if(!dialogKind)dialog.current?.close(); },[dialogKind]);
  useEffect(()=>{ if(answer)answerRef.current?.scrollIntoView({behavior:'smooth',block:'start'}); },[answer]);
  useEffect(()=>{if(loaded)setWikis(previous=>syncExpertWikis(previous,categories,library,new Date().toISOString()));},[loaded,categories,library]);
  const reviewWiki=(id:string)=>{
    const page=wikis.find(item=>item.id===id);if(!page)return;
    const count=sharedQuestionCount(page,history);
    const reviewed=reviewWikiQuestions(page,new Date().toISOString(),count,library);
    setWikis(previous=>previous.map(item=>item.id===id?reviewed:item));
    notify(reviewed.revisions.length>page.revisions.length?'담당 논문의 근거로 FAQ를 보강하고 변경 이력에 기록했습니다. · 화면 예시':reviewed.faqEnabled?'관련 근거를 다시 점검했습니다. 추가할 내용이 없어 Wiki 버전은 유지됩니다.':'확인된 주장 근거가 없어 FAQ 보강을 보류했습니다.');
  };

  const changeVisibility = (item:Answer) => {
    if(item.authorId!=='me'||item.deleted)return;
    const updated:Answer={...item,visibility:item.visibility==='team'?'private':'team'};
    setHistory(prev=>prev.map(entry=>entry.id===item.id?updated:entry));
    if(answer?.id===item.id)setAnswer(updated);
    notify(updated.visibility==='private'?'나만 볼 수 있는 대화로 변경했습니다. · 화면 예시':'팀의 질문에 표시했습니다. · 공유 화면 예시');
  };
  const changeDeleted = (item:Answer) => {
    if(item.authorId!=='me')return;
    const updated={...item,deleted:!item.deleted};
    setHistory(prev=>prev.map(entry=>entry.id===item.id?updated:entry));
    if(answer?.id===item.id){setAnswer(null);navigate('personal');}
    notify(updated.deleted?'내 페이지의 휴지통으로 옮겼습니다.':'대화를 복구했습니다.');
  };
  const ask = (text=question,mode?:Answer['mode']) => {
    if(thinking || !text.trim() || !loaded)return;
    const routed=selectExperts(text,categories,expertMode,selectedExpertIds);
    setProposal({text:text.trim(),mode:mode||(text.includes('비교')?'compare':text.includes('한계')?'limits':text.includes('주장')||text.includes('해석')?'claims':'summary'),ids:routed.map(item=>item.id),selection:expertMode});
    setAnswer(null);setQuestion(text);navigate('ask');
  };
  const respond = () => {
    if(!proposal||thinking)return;
    const candidates=hasPaperScope?readyLibrary.filter(p=>selected.includes(p.id)):readyLibrary;
    const chosen=categories.filter(item=>item.enabled&&proposal.ids.includes(item.id));
    const contributions=buildContributions(proposal.text,candidates,chosen);
    const paperIds=[...new Set(contributions.flatMap(item=>item.paperIds))];
    if(!paperIds.length)return;
    const newAnswer:Answer={id:crypto.randomUUID(),question:proposal.text,mode:proposal.mode,paperIds,authorId:'me',visibility,experts:contributions.filter(item=>item.paperIds.length).map(item=>item.name),expertSelection:proposal.selection,contributions,evidence:captureAnswerEvidence(contributions.flatMap(item=>item.claimMatches))};
    setProposal(null);setQuestion('');setThinking(true);
    later(()=>{setAnswer(newAnswer);setHistory(prev=>[newAnswer,...prev]);setThinking(false);},650);
  };
  const scan = () => {
    if(scanning)return;
    if(scanDone){notify('새로 추가된 논문이 없습니다.');return;}
    setScanning(true);setWikiPhase('checking');
    later(()=>setWikiPhase('updating'),850);
    later(()=>{setScanning(false);setScanDone(true);setWikiPhase('done');setLibrary(prev=>[...incoming.filter(p=>!prev.some(x=>x.id===p.id)),...prev]);setCategories(prev=>prev.map(expert=>{const membership=previewMembership(expert.facets||{},incoming,expert);return {...expert,paperIds:[...new Set([...expert.paperIds,...membership.paperIds])],reviewIds:[...new Set([...(expert.reviewIds||[]),...membership.reviewIds])],updatedAt:membership.paperIds.length?knowledgeDate:expert.updatedAt};}));notify('예시 논문 2편을 추가했습니다. 관련 전문가의 Wiki에서 달라진 내용을 확인하세요.');},2200);
  };
  const retry = () => { setReviewRetry(true);setDialogKind(null);later(()=>{setReviewRetry(false);notify('아직 내용을 읽기 어렵습니다. 원본을 확인해 주세요. · 예시 결과');},1700); };

  const copyAnswer = async () => {
    if(!answer)return;
    try { await navigator.clipboard.writeText(answerPlainText(answer,evidence));notify('예시 답변과 출처를 복사했습니다.'); } catch {notify('브라우저에서 클립보드 접근을 허용하지 않았습니다.');}
  };
  const exportLibrary = () => {
    const csv = '\uFEFF'+[['type','title','authors','year','topic','filename'],...filtered.map(p=>['fictional_prototype',p.title,p.authors,p.year,p.topic,p.filename])].map(row=>row.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\r\n');
    const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8;'}));const a=document.createElement('a');a.href=url;a.download='evidence-studio-example-library.csv';a.click();later(()=>URL.revokeObjectURL(url),1000);notify('현재 목록의 예시 서지정보를 내보냈습니다.');
  };

  return <div className="app-shell">
    {mobile&&<button className="sidebar-shade" aria-label="메뉴 닫기" onClick={()=>setMobile(false)}/>}
    <aside className={`sidebar ${mobile?'is-open':''}`}>
      <div className="sidebar-brand"><Brand/><button className="icon-button mobile-only" onClick={()=>setMobile(false)} aria-label="메뉴 닫기"><X/></button></div>
      <button className="new-chat" onClick={resetConversation} disabled={thinking}><Plus size={17}/>새 질문 시작<span>↗</span></button>
      <div className="nav-label">RESEARCH</div>
      <nav aria-label="주 메뉴">{(['ask','experts','library','team'] as View[]).map(key=>{const Icon=navIcons[key];return <button key={key} className={`nav-item ${view===key?'active':''}`} onClick={()=>navigate(key)} aria-current={view===key?'page':undefined}><Icon size={18}/>{views[key]}{key==='library'&&<span className="nav-count">{library.length}</span>}</button>;})}</nav>
      <div className="recent-heading"><span className="nav-label">내 최근 질문</span><Clock3 size={13}/></div>
      <div className="recent-list">{history.filter(item=>item.authorId==='me'&&!item.deleted).length?history.filter(item=>item.authorId==='me'&&!item.deleted).slice(0,5).map(item=><button key={item.id} onClick={()=>{setProposal(null);setAnswer(item);navigate('ask');}} title={item.question}><MessageSquare size={14}/><span>{item.question}</span></button>):<p>새로운 탐구가 이곳에 쌓입니다.</p>}</div>
      <div className="sidebar-bottom"><div className="corpus-card"><div><span className="status-dot"/><strong>팀의 논문 라이브러리</strong></div><p><b>{library.length}</b>편의 논문 <span>·</span> 예시 데이터</p><button onClick={()=>navigate('ingest')}>자료 현황 보기<ArrowUpRight size={14}/></button></div>
        {isAdmin&&<button className={`nav-item ${view==='settings'?'active':''}`} onClick={()=>navigate('settings')}><Settings2 size={18}/>전문가 설정</button>}
        <button className="nav-item" onClick={()=>setDialogKind('guide')}><CircleHelp size={18}/>사용 가이드</button>
        <button className="profile profile-button" onClick={()=>navigate('personal')}><span className="user-avatar">나</span><span><strong>내 페이지</strong><small>내 질문 · 북마크 · 휴지통</small></span><ChevronRight size={15}/></button>
      </div>
    </aside>
    <div className="workspace-main">
      <header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-only" aria-label="메뉴 열기" onClick={()=>setMobile(true)}><Menu/></button><strong>{views[view]}</strong></div><div className="topbar-right"><button className="team-members-button" onClick={()=>setDialogKind('members')} aria-label="팀 멤버 4명 보기"><span className="avatar-stack">{members.slice(0,3).map(m=><span key={m.id}>{m.initial}</span>)}</span><span>멤버 4명</span></button><span className="prototype-badge"><span/>시제품</span><button className="icon-button" aria-label="사용 가이드" onClick={()=>setDialogKind('guide')}><CircleHelp size={18}/></button></div></header>
      <main id="main" tabIndex={-1}>
      {loadError?<div className="error-state"><AlertCircle/><h2>라이브러리를 불러오지 못했습니다</h2><p>{loadError}</p><button className="primary" onClick={init}>다시 연결</button></div>:!loaded?<div className="empty-state"><RotateCw className="spin"/><p>팀의 라이브러리를 준비하고 있습니다.</p></div>:<>
      {view==='ask'&&<div className={`ask-layout ${answer?'has-answer':''}`}>
        <section className="conversation">
          <div className="section-top"><div className="eyebrow"><span className="status-dot"/>YOUR RESEARCH COMPANION</div><button className="quiet-button" onClick={()=>navigate('library')}><Library size={14}/>논문 {readyLibrary.length}편 연결<ChevronRight size={14}/></button></div>
          {!answer&&!thinking&&!proposal?<div className="welcome"><div className="welcome-mark"><Layers3 size={34}/><span className="orbit orbit-one"/><span className="orbit orbit-two"/><i/></div><span className="welcome-kicker">FROM PAPERS TO PERSPECTIVE</span><h1>읽어 둔 논문이,<br/><span>새로운 관점이 되도록.</span></h1><p>흩어진 지식을 연결하고, 근거를 따라 탐구하세요.<br/>팀이 함께 쌓은 논문에서 답을 찾는 연구 파트너입니다.</p><div className="topic-pill"><BookOpen size={14}/>반도체 메모리<span>DRAM · NAND · HBM</span></div>
            <div className="suggestions">{suggestions.map((item,i)=>{const Icon=[FileSearch,Layers3,Sparkles][i];return <button key={item.text} onClick={()=>ask(item.text,item.mode)}><Icon size={19}/><strong>{item.title}</strong><span>{item.desc}</span><ArrowUpRight size={15}/></button>;})}</div>
          </div>:<div className="answer-thread">
            {proposal?<ExpertRecommendation question={proposal.text} categories={categories} papers={hasPaperScope?readyLibrary.filter(paper=>selected.includes(paper.id)):readyLibrary} selectedIds={proposal.ids} onSelection={ids=>setProposal({...proposal,ids,selection:'manual'})} onConfirm={respond} onCancel={()=>{setQuestion(proposal.text);setProposal(null);later(()=>document.getElementById('question')?.focus(),50);}}/>:thinking?<div className="thinking"><div className="question-bubble">예시 대화를 구성하고 있습니다</div><Brand small/><p><span className="loading-dots">•••</span> 논문의 근거와 함께 예시 답변을 준비합니다.</p></div>:answer&&<>
              <div className="question-label"><span className="user-avatar">연</span><span>{memberName(answer.authorId)}의 질문</span><span className="conversation-visibility">{answer.visibility==='team'?<Users size={13}/>:<Lock size={13}/>} {answer.visibility==='team'?'팀에 공유됨':'나만 보기'}</span></div><h1 ref={answerRef} className="question-title">{answer.question}</h1>
              <div className="answer-author"><Brand small/><strong>Evidence Studio</strong><Tag tone="green">출처 {evidence.length}편</Tag><span className="muted">답변 구조 예시</span></div>
              <div className="demo-inline"><Sparkles size={15}/>가상 문헌에서 질문에 맞는 주장과 연결 수치를 고른 예시입니다. 실제 AI 분석·검색은 수행하지 않습니다.</div>
              <div className="answer-experts"><span>답변에 참여한 전문가</span>{answer.experts?.length?answer.experts.map(name=><Tag tone="blue" key={name}>{name}</Tag>):<Tag>참여 전문가 기록 없음</Tag>}{answer.expertSelection&&<small>{answer.expertSelection==='manual'?'직접 선택':'자동 선택'}</small>}</div><ExpertContributions answer={answer} papers={evidence} onPaper={paper=>showPaper(paper,'claims')}/><AnswerEvidence answer={answer} papers={evidence} onPaper={showPaper}/><div className="answer-actions"><button onClick={copyAnswer}><Copy size={15}/>답변 복사</button>{answer.authorId==='me'&&<><button onClick={()=>changeVisibility(answer)}>{answer.visibility==='team'?<Lock size={15}/>:<Share2 size={15}/>} {answer.visibility==='team'?'비공개로 전환':'팀에 공유'}</button><button onClick={()=>changeDeleted(answer)}><Trash2 size={15}/>삭제</button></>}</div>
              <div className="followup"><span>이어서 탐구하기</span><button onClick={()=>ask(`${evidence[0]?.topic||'메모리'} 연구에서 확인할 조건과 한계는?`,'limits')}>평가 조건 살펴보기<ArrowRight size={14}/></button></div>
            </>}
          </div>}
          {!proposal&&<div className="composer-area">
            <form className="composer" onSubmit={(e:FormEvent)=>{e.preventDefault();ask();}}>
              {hasPaperScope&&<div className="selected-paper-context"><BookOpen size={14}/><span>선택한 논문 <strong>{selected.length}편</strong>으로 질문</span><button type="button" aria-label="논문 선택 범위 해제" title="선택 논문 제한 해제" onClick={()=>setScope('all')}><X size={14}/></button></div>}
              <label className="sr-only" htmlFor="question">연구 질문 입력</label>
              <textarea id="question" value={question} onChange={e=>setQuestion(e.target.value)} placeholder="논문에 묻고 싶은 질문을 입력하세요…" maxLength={2000} rows={2} disabled={thinking} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();ask();}}}/>
              <div className="composer-footer">
                <QuestionOptions visibility={visibility} onVisibility={setVisibility} mode={expertMode} onMode={setExpertMode} categories={categories} selectedIds={selectedExpertIds} onSelection={setSelectedExpertIds} disabled={thinking}/>
                <button type="submit" className="send-button" aria-label="질문 보내기" disabled={!question.trim()||thinking||manualSelectionMissing}>{thinking?<RotateCw size={19} className="spin"/>:<ArrowUp size={20}/>}</button>
              </div>
            </form>
            <p className="composer-note"><ShieldCheck size={12}/>{visibility==='team'?'팀원이 질문과 답변을 함께 볼 수 있습니다.':'이 질문과 답변은 나만 볼 수 있습니다.'} · 예시 답변<span>Enter 전송 · Shift + Enter 줄바꿈</span></p>
          </div>}
        </section>
        <aside className="evidence-panel"><div className="panel-heading"><span><Quote size={18}/>근거 노트</span><Tag>{evidence.length.toString().padStart(2,'0')}</Tag></div>{evidence.length?<div className="evidence-list"><p className="panel-intro">선택된 주장의 논문과 대표 원문입니다. 각 주장의 원문·조건은 답변에서 펼쳐 볼 수 있습니다.</p>{evidence.map((p,i)=><button className="evidence-card" key={p.id} onClick={()=>showPaper(p,'claims')}><div><span className="source-number">{i+1}</span><span>{p.year} · 가상 문헌</span><ArrowUpRight size={14}/></div><h3>{p.title}</h3><p>{answer&&selectedStatement(answer,p.id)?.quote||'연결된 원문 없음'}</p><footer><FileText size={12}/>{answer&&selectedStatement(answer,p.id)?`p. ${selectedStatement(answer,p.id)!.page} · ${selectedStatement(answer,p.id)!.section}`:'연결된 근거 없음'}</footer></button>)}</div>:<div className="evidence-empty"><div className="paper-illustration"><div/><div><span/><span/><span/><Quote size={17}/></div></div><h3>모든 답에는<br/>출발점이 있습니다.</h3><p>질문을 시작하면 참조한 논문과<br/>답변의 근거를 이곳에서 확인합니다.</p><div className="evidence-features"><span><Check size={13}/>논문과 답변을 연결하는 인용</span><span><Check size={13}/>원문 페이지와 문맥 확인</span><span><Check size={13}/>근거 부족과 검토 상태 표시</span></div></div>}<div className="panel-bottom"><span className="mini-line"/><small>GOOD QUESTIONS START<br/>WITH GOOD SOURCES.</small></div></aside>
      </div>}
      {view==='experts'&&<ExpertKnowledge categories={categories} papers={library} wikis={wikis} history={history} wikiPhase={wikiPhase} onScan={scan} onReviewWiki={reviewWiki} selectedId={knowledgeExpertId} onSelect={setKnowledgeExpertId} isAdmin={isAdmin} onManage={()=>navigate('settings')} onPaper={showPaper} onAsk={expert=>{setExpertMode('manual');setSelectedExpertIds([expert.id]);setScope('all');setProposal(null);setAnswer(null);setQuestion(expert.name+' 분야에서 주목할 논문과 최근 이슈를 정리해 줘');navigate('ask');later(()=>document.getElementById('question')?.focus(),50);}}/>}
      {view==='library'&&<div className="page-content"><div className="page-heading"><div><div className="eyebrow">YOUR KNOWLEDGE, CONNECTED</div><h1>논문 라이브러리<span className="title-count">{library.length}</span></h1><p>축적한 논문을 탐색하고, 질문에 사용할 근거를 선택하세요.</p></div><div className="library-heading-actions"><button className="secondary" onClick={()=>navigate('ingest')}><FolderSync size={16}/>자료 현황 보기</button><button className="primary" onClick={()=>setDialogKind('upload')}><Upload size={16}/>논문 추가</button></div></div>
        <div className="library-summary"><div><BookOpen size={20}/><span>전체 논문<strong>{library.length}<small>편</small></strong></span></div><div><Layers3 size={20}/><span>연구 주제<strong>{new Set(library.map(p=>p.topic)).size}<small>개</small></strong></span></div><div><ShieldCheck size={20}/><span>데이터 상태<strong className="text-stat">화면 확인용 예시</strong></span></div><div className="summary-note">실제 논문을 연결하면<br/>팀의 연구 지식이 쌓입니다.<ArrowUpRight size={25}/></div></div>
        <div className="library-tabs"><button className={!onlySaved?'active':''} onClick={()=>setOnlySaved(false)}>모든 논문 <span>{library.length}</span></button><button className={onlySaved?'active':''} onClick={()=>setOnlySaved(true)}><Bookmark size={14}/>북마크 <span>{bookmarks.length}</span></button><button className="export-button" onClick={exportLibrary}><ArrowDownToLine size={15}/>CSV 내보내기</button></div>
        <div className="filter-bar"><label className="search-input"><Search size={17}/><input id="paper-search" aria-label="논문 검색" value={search} onChange={e=>setSearch(e.target.value)} placeholder="제목, 저자, 키워드로 검색"/><kbd>Ctrl K</kbd></label><label className="filter-select"><SlidersHorizontal size={14}/><select aria-label="주제 필터" value={topic} onChange={e=>setTopic(e.target.value)}><option>전체 주제</option>{[...new Set(library.map(p=>p.topic))].map(t=><option key={t}>{t}</option>)}</select></label><select className="year-select" aria-label="연도 필터" value={year} onChange={e=>setYear(e.target.value)}><option>전체 연도</option>{[...new Set(library.filter(p=>p.year>0).map(p=>p.year))].sort((a,b)=>b-a).map(y=><option key={y}>{y}</option>)}</select></div>
        <div className="table-wrap"><table className="paper-table"><thead><tr><th><input type="checkbox" aria-label="표시된 논문 전체 선택" disabled={!selectable.length} checked={selectable.length>0&&selectable.every(p=>selected.includes(p.id))} onChange={e=>setSelected(prev=>e.target.checked?[...new Set([...prev,...selectable.map(p=>p.id)])]:prev.filter(id=>!selectable.some(p=>p.id===id)))}/></th><th>논문</th><th>연도</th><th>주제</th><th>상태</th><th><span className="sr-only">북마크</span></th></tr></thead><tbody>{filtered.map(p=><tr key={p.id} className={selected.includes(p.id)?'selected':''}><td><input type="checkbox" aria-label={`${p.title} 선택`} disabled={p.status==='pending'} checked={selected.includes(p.id)} onChange={()=>toggleSelected(p.id)}/></td><td><button className="paper-title" onClick={()=>showPaper(p)}><span className="file-icon"><FileText size={21}/></span><span><strong>{p.title}</strong><small>{p.status==='pending'?`${uploaderLabel(p.uploadedBy||'me')} 추가 · 서지정보 확인 중`:<>{p.authors} <span>·</span> {p.pages} pages</>}</small></span></button></td><td>{p.year||'—'}</td><td><Tag>{p.topic}</Tag></td><td>{p.status==='pending'?<span className="pending-status"><Clock3 size={12}/>준비 중</span>:<span className="ready-status"><span/>검색 가능<span className="sr-only"> 예시 상태</span></span>}</td><td><button className={`icon-button bookmark ${bookmarks.includes(p.id)?'saved':''}`} aria-label={`${p.title} 북마크 ${bookmarks.includes(p.id)?'해제':'추가'}`} aria-pressed={bookmarks.includes(p.id)} onClick={()=>setBookmarks(prev=>prev.includes(p.id)?prev.filter(id=>id!==p.id):[...prev,p.id])}><Bookmark size={17}/></button></td></tr>)}</tbody></table>{!filtered.length&&<div className="empty-state"><Search size={26}/><h3>조건에 맞는 논문이 없습니다</h3><p>검색어와 필터를 바꾸어 보세요.</p><button className="secondary" onClick={()=>{setSearch('');setTopic('전체 주제');setYear('전체 연도');setOnlySaved(false);}}>필터 초기화</button></div>}</div><div className="table-footer"><span>{filtered.length}편 표시 · 논문 처리와 서지정보는 화면 예시입니다.</span><span>제목을 누르면 상세 정보를 확인할 수 있습니다.</span></div>{selected.length>0&&<div className="selection-bar"><span><CheckCheck size={17}/><strong>{selected.length}편 선택됨</strong></span><button onClick={()=>setSelected([])}>선택 해제</button><button className="primary" onClick={()=>{setScope('selected');resetConversation();}}>선택한 논문으로 질문<ArrowRight size={15}/></button></div>}
      </div>}
      {(view==='team'||view==='personal')&&<ConversationsView personal={view==='personal'} history={history} bookmarks={bookmarks.length} onOpen={item=>{setProposal(null);setAnswer(item);navigate('ask');}} onVisibility={changeVisibility} onDelete={changeDeleted} onNew={resetConversation} onBookmarks={()=>{setOnlySaved(true);navigate('library');}}/>}
      {view==='ingest'&&<div className="page-content"><div className="page-heading"><div><div className="eyebrow">READY FOR YOUR NEXT QUESTION</div><h1>자료 현황</h1><p>팀이 함께 사용할 논문과 확인이 필요한 자료를 살펴보세요.</p></div><button className="secondary" onClick={scan} disabled={scanning}><RotateCw size={16} className={scanning?'spin':''}/>{scanning?'확인 중…':'새 논문 확인'}</button></div>
        <div className="collection-overview"><div><span className="overview-icon"><BookOpen size={21}/></span><span>질문할 수 있는 논문<strong>{readyLibrary.length}<small>편</small></strong></span></div><div><span className="overview-icon"><Clock3 size={21}/></span><span>준비 중<strong>{(scanDone?0:2)+pendingUploads.length}<small>편</small></strong></span></div><div><span className="overview-icon amber"><AlertCircle size={21}/></span><span>확인 필요<strong>1<small>편</small></strong></span></div></div>
        <section className="queue-panel"><div className="block-heading"><h2>최근 추가된 자료</h2><Tag>예시 자료</Tag></div>{pendingUploads.map(p=><div className="queue-row" key={p.id}><span className="file-icon"><FileText size={20}/></span><div><strong>{p.filename}</strong><small>{uploaderLabel(p.uploadedBy||'me')} 추가 · 준비 상태 예시</small></div><Tag>준비 중</Tag></div>)}{incoming.map(p=><div className="queue-row" key={p.id}><span className="file-icon"><FileText size={20}/></span><div><strong>{p.title}</strong><small>{scanDone?'팀 라이브러리에 추가되었습니다.':'곧 질문에 사용할 수 있습니다.'}</small></div>{scanDone?<button className="quiet-button" onClick={()=>showPaper(p)}>논문 보기<ArrowUpRight size={14}/></button>:<Tag tone={scanning?'blue':'neutral'}>{scanning?'준비 중':'대기 중'}</Tag>}</div>)}
          <div className="queue-row review-row"><span className="file-icon amber"><FileText size={20}/></span><div><strong>scanned_paper_03.pdf</strong><small>{reviewRetry?'다시 확인하고 있습니다.':'일부 내용을 읽기 어려워 확인이 필요합니다.'}</small></div><button onClick={()=>setDialogKind('review')} disabled={reviewRetry}><Tag tone="amber">{reviewRetry?'확인 중':'확인하기'}<ChevronRight size={12}/></Tag></button></div>
          <div className="queue-row"><span className="file-icon"><Copy size={20}/></span><div><strong>dram_retention_copy.pdf</strong><small>이미 라이브러리에 있는 논문입니다.</small></div><Tag>등록됨</Tag></div></section>
        {scanDone&&<div className="scan-result"><CheckCheck size={21}/><div><strong>새 논문 2편으로 질문할 수 있어요.</strong><span>관련 전문가의 Wiki에 추가된 지식과 변경 이력을 확인하세요.</span></div><button className="secondary" onClick={()=>navigate('experts')}>Wiki 갱신 확인<ArrowRight size={14}/></button></div>}
        <p className="page-footnote">가상 자료를 사용한 화면 예시입니다. 실제 파일을 처리하지 않습니다.</p>
      </div>}
      {view==='settings'&&isAdmin&&<div className="page-content settings-page"><div className="page-heading"><div><div className="eyebrow">EXPERT PERSPECTIVES</div><h1>전문가 설정</h1><p>전문가 개요와 논문 분류를 설정하고, 지식 현황을 관리하세요.</p></div><Tag>관리자 전용</Tag></div><ExpertSettings categories={categories} papers={library} onKnowledge={id=>{setKnowledgeExpertId(id);navigate('experts');}} onSave={value=>{if(!isAdmin)return;setCategories(value);try{localStorage.setItem('evidence-studio.experts.v3',JSON.stringify(value));notify('전문가 설정을 저장했습니다. 다음 예시 질문부터 적용됩니다.');}catch{notify('화면에 적용했습니다. 브라우저 저장은 사용할 수 없습니다.');}}}/></div>}
      {view==='settings'&&!isAdmin&&<div className="empty-state access-denied"><Lock size={28}/><h2>관리자만 접근할 수 있습니다</h2><p>전문가 설정은 관리자가 변경할 수 있습니다.</p><button className="primary" onClick={()=>navigate('ask')}>연구 질문으로 돌아가기<ArrowRight size={15}/></button></div>}
      </>}
      </main>
    </div>
    {toast&&<div className="toast" role="status"><Check size={17}/>{toast}<button aria-label="알림 닫기" onClick={()=>setToast('')}><X size={15}/></button></div>}
    <dialog ref={dialog} className={`detail-dialog ${dialogKind==='paper'?'paper-dialog':''}`} onCancel={()=>setDialogKind(null)} onClick={e=>{if(e.target===e.currentTarget)setDialogKind(null);}} aria-labelledby="dialog-title"><div className="dialog-toolbar"><button className="dialog-close icon-button" onClick={()=>setDialogKind(null)} aria-label="상세 창 닫기"><X size={20}/></button></div><div className="dialog-content">
      {dialogKind==='upload'&&<PaperUpload onUpload={addPapers}/>}
      {dialogKind==='paper'&&activePaper?.status==='pending'&&<div className="pending-paper"><Tag tone="amber">준비 중</Tag><h2 id="dialog-title">{activePaper.title}</h2><p className="muted">{uploaderLabel(activePaper.uploadedBy||'me')} 팀 라이브러리에 추가한 논문입니다.</p><div className="detail-file"><FileText size={19}/><span>{activePaper.filename}</span></div><p className="abstract">논문이 준비되면 서지정보와 근거를 확인하고 질문할 수 있습니다.</p><div className="demo-inline">파일명과 준비 상태를 보여주는 목업입니다. 실제 업로드나 PDF 분석은 수행하지 않습니다.</div></div>}
      {dialogKind==='paper'&&activePaper&&activePaper.status!=='pending'&&<PaperEvidence key={activePaper.id+paperTab} paper={activePaper} papers={library} initialTab={paperTab} onPaper={paper=>showPaper(paper,'claims')} onAsk={()=>{setSelected([activePaper.id]);setScope('selected');setDialogKind(null);resetConversation();}}/>}
      {dialogKind==='review'&&<><Tag tone="amber">확인 필요</Tag><h2 id="dialog-title">이 논문은 내용을 읽기 어려워요.</h2><p className="muted">scanned_paper_03.pdf</p><div className="review-details"><div><span>확인할 부분</span><strong>3페이지의 글자와 수식</strong></div><div><span>권장 조치</span><strong>원본이 선명한지 확인해 주세요.</strong></div></div><p className="abstract">이 자료를 제외한 논문으로는 계속 질문할 수 있습니다.</p><div className="demo-inline">화면 예시입니다. 실제 파일은 연결되지 않았습니다.</div><button className="primary full" onClick={retry}><RotateCw size={16}/>다시 확인</button></>}
      {dialogKind==='members'&&<><div className="eyebrow">OUR RESEARCH TEAM</div><h2 id="dialog-title">함께 연구하는 사람들</h2><p className="muted">하나의 라이브러리에서 지식을 나눕니다.</p><div className="member-list">{members.map(m=><div key={m.id}><span className="user-avatar">{m.initial}</span><strong>{m.name}</strong><Tag>{m.id==='me'?roleLabels[currentRole]:m.role}</Tag></div>)}</div><p className="abstract">모든 멤버가 논문을 추가하고 질문과 답변을 공유합니다. 전문가 설정은 관리자가 관리합니다.</p><div className="demo-inline">가상 멤버로 구성된 시제품입니다. 실제 로그인과 동시 접속은 아직 연결되지 않았습니다.</div></>}
      {dialogKind==='guide'&&<><div className="eyebrow">A LITTLE GUIDE</div><h2 id="dialog-title">함께 쌓은 논문에서,<br/>다음 질문을 찾아보세요.</h2><div className="guide-steps"><div><span>1</span><section><h3>팀의 논문 탐색</h3><p>공용 라이브러리에서 논문을 찾아 읽고, 질문에 사용할 자료를 선택하세요.</p></section></div><div><span>2</span><section><h3>전문가를 확인하고 질문하기</h3><p>전문가 지식에서 담당 자료를 살펴보세요. 질문을 보내면 추천 이유를 확인하고, 각 전문가의 지식에서 근거와 답변을 볼 수 있습니다. 대화는 기본적으로 팀에 공유됩니다.</p></section></div><div><span>3</span><section><h3>내 기록 관리</h3><p>내 페이지에서 내가 쓴 대화를 비공개로 바꾸거나 삭제할 수 있습니다. 삭제한 대화는 휴지통에서 복구하세요.</p></section></div></div><div className="demo-inline">모든 논문·멤버·답변은 예시입니다. 공유·삭제는 이 화면에서만 바뀌고 새로고침하면 초기화됩니다.</div><button className="primary full" onClick={()=>{setDialogKind(null);resetConversation();}}>질문 시작하기<ArrowRight size={15}/></button></>}
    </div></dialog>
  </div>;
}

function ConversationsView({personal,history,bookmarks,onOpen,onVisibility,onDelete,onNew,onBookmarks}:{personal:boolean;history:Answer[];bookmarks:number;onOpen:(item:Answer)=>void;onVisibility:(item:Answer)=>void;onDelete:(item:Answer)=>void;onNew:()=>void;onBookmarks:()=>void}){
  const [filter,setFilter]=useState<'all'|'private'|'trash'>('all');
  const visible=history.filter(item=>personal?item.authorId==='me'&&(filter==='trash'?item.deleted:!item.deleted&&(filter!=='private'||item.visibility==='private')):!item.deleted&&item.visibility==='team');
  return <div className="page-content conversations-page"><div className="page-heading"><div><div className="eyebrow">{personal?'YOUR QUESTIONS, YOUR WAY':'QUESTIONS WE EXPLORE TOGETHER'}</div><h1>{personal?'내 페이지':'팀의 질문'}</h1><p>{personal?'내가 쓴 대화와 저장한 논문을 모아 봅니다.':'동료의 질문에서 발견한 관점으로 연구를 이어가세요.'}</p></div><button className="primary" onClick={onNew}><Plus size={16}/>새 질문</button></div>
    {personal&&<div className="personal-toolbar"><div className="personal-tabs" role="group" aria-label="내 대화 필터"><button className={filter==='all'?'active':''} onClick={()=>setFilter('all')}>내 대화</button><button className={filter==='private'?'active':''} onClick={()=>setFilter('private')}><Lock size={14}/>비공개</button><button className={filter==='trash'?'active':''} onClick={()=>setFilter('trash')}><Trash2 size={14}/>휴지통</button></div><button className="secondary" onClick={onBookmarks}><Bookmark size={15}/>내 북마크 {bookmarks}</button></div>}
    <div className="conversation-cards">{visible.map(item=><article className="conversation-card" key={item.id}><div className="conversation-card-meta"><span className="user-avatar">{memberName(item.authorId).slice(0,1)}</span><strong>{memberName(item.authorId)}</strong><span>{item.deleted?'휴지통':item.visibility==='team'?'팀에 공유됨':'나만 보기'}</span></div><button className="conversation-card-title" onClick={()=>onOpen(item)} disabled={!!item.deleted}>{item.question}<ArrowUpRight size={17}/></button><div className="conversation-card-footer"><span><BookOpen size={14}/>참고 논문 {item.paperIds.length}편</span>{item.authorId==='me'&&<div>{item.deleted?<button onClick={()=>onDelete(item)}><Undo2 size={14}/>복구</button>:<><button onClick={()=>onVisibility(item)}>{item.visibility==='team'?<Lock size={14}/>:<Share2 size={14}/>} {item.visibility==='team'?'비공개로 전환':'팀에 공유'}</button><button onClick={()=>onDelete(item)}><Trash2 size={14}/>삭제</button></>}</div>}</div></article>)}</div>
    {!visible.length&&<div className="empty-state"><MessageSquare size={28}/><h3>{filter==='trash'&&personal?'휴지통이 비어 있습니다.':'아직 표시할 대화가 없습니다.'}</h3><p>{personal?'질문을 시작하면 내 기록이 이곳에 쌓입니다.':'팀에 공유한 질문과 답변이 이곳에 모입니다.'}</p></div>}
    <p className="page-footnote">{personal&&filter==='trash'?'복구하면 삭제 전 공개 범위로 돌아갑니다. · ':''}가상 대화로 구성된 화면 예시입니다. 공유와 삭제는 다른 사용자에게 전달되지 않습니다.</p>
  </div>;
}
