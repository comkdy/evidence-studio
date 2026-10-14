import { useEffect, useState } from 'react';
import { ArrowUpRight, ChevronLeft, ChevronRight, FileText } from 'lucide-react';
import { get, kinds, time } from './pilotData';
import { scientificNotation as numberText } from './scientificNotation';
import type { Claim, CorpusStatus, Paper, Source, Wiki, WikiDetail } from './pilotData';
import { WikiMarkdown } from './WikiMarkdown';
import { boundedPage } from './workspaceState';
import { SourceReviews } from './SourceReviews';

export function AbstractOverview({paper}:{paper:Pick<Paper,'abstract_summary'>}){
 return <section className="pilot-abstract-summary" aria-label="초록 요약"><div className="pilot-abstract-label">초록 요약<span>AI 요약</span></div><p>{paper.abstract_summary?numberText(paper.abstract_summary.text):'초록 요약이 아직 없습니다. 원본 PDF를 확인해 주세요.'}</p></section>;
}

export function SourceView({source,documentId}:{source:Source;documentId?:string}){
 const sourceDocument=source.document_id||documentId;
 return <div className="pilot-quote"><blockquote>{source.quote}</blockquote><span>p. {source.page_number} · {source.locator}</span>{sourceDocument&&<a href={'/api/pilot/documents/'+encodeURIComponent(sourceDocument)+'/pdf#page='+source.page_number} target="_blank" rel="noreferrer">PDF에서 보기<ArrowUpRight size={13}/></a>}</div>;
}
export function ClaimCard({claim,documentId,onPaper}:{claim:Claim;documentId?:string;onPaper?:(id:string)=>void}){
 const sources=[...new Map([...claim.sources,...claim.measurements.flatMap(m=>[...m.sources,...m.conditions.flatMap(c=>c.source?[c.source]:[])])].map(s=>[s.id,s])).values()];
 return <article className="pilot-claim"><div className="pilot-labels"><span>{kinds[claim.kind]}</span><small>원문 대조 · 사람 검토 대기</small></div><h3>{numberText(claim.summary)}</h3><p className="pilot-scope">{numberText(claim.scope)}</p>
 {claim.title&&onPaper&&<button className="pilot-source-title" onClick={()=>onPaper(claim.paper_id)}><FileText size={13}/>{claim.title}<ArrowUpRight size={12}/></button>}
 {!!claim.measurements.length&&<div className="pilot-numbers">{claim.measurements.map(m=><div key={m.id}><strong className="pilot-measure-label">{m.label}</strong><span className="pilot-measure-value">{numberText(m.raw_value)}</span><small>{kinds[m.kind]} · {numberText(m.sample)}</small><p>{m.conditions.map(c=>c.name+': '+numberText(c.raw_value??'미기재')).join(' · ')}</p></div>)}</div>}
 <details><summary>원문 근거 {sources.length}개 · 연결 수치 {claim.measurements.length}개</summary>{sources.map(s=><SourceView key={s.id} source={s} documentId={documentId||claim.document_id}/>)}</details></article>;
}
export function Pager({page,total,size,onPage,label}:{page:number;total:number;size:number;onPage:(page:number)=>void;label:string}){
 return <div className="pilot-pagination"><button disabled={page===0} aria-label={label+' 이전 페이지'} onClick={()=>onPage(page-1)}><ChevronLeft size={16}/></button><span>{page+1} / {Math.max(1,Math.ceil(total/size))}</span><button disabled={(page+1)*size>=total} aria-label={label+' 다음 페이지'} onClick={()=>onPage(page+1)}><ChevronRight size={16}/></button></div>;
}
export function WikiCard({wiki,onPaper,refreshKey=0}:{wiki:Wiki;onPaper:(id:string)=>void;refreshKey?:number}){
 const [open,setOpen]=useState(false),[page,setPage]=useState(0),[data,setData]=useState<WikiDetail|null>(null),[error,setError]=useState('');
 useEffect(()=>{if(!open)return;const ctrl=new AbortController();setData(null);setError('');get<WikiDetail>('/wikis/'+encodeURIComponent(wiki.id)+'?limit=3&offset='+page*3,ctrl.signal).then(value=>{if(!ctrl.signal.aborted){setData(value);setPage(boundedPage(page,value.total_claims,3));}}).catch(e=>{if(!ctrl.signal.aborted)setError(e.message);});return()=>ctrl.abort();},[open,page,wiki.id,wiki.revision,wiki.created_at,refreshKey]);
 return <article className="pilot-wiki"><div className="pilot-labels"><span>{wiki.scope==='common'?'공통 지식':wiki.expert_name}</span>{wiki.revision&&<span>v{wiki.revision}</span>}<small>마지막 갱신 {time(wiki.created_at)} KST</small></div><h2>{numberText(wiki.title)}</h2><p>{numberText(wiki.body)}</p>{wiki.limitation&&<aside>{numberText(wiki.limitation)}</aside>}
 {wiki.status==='awaiting_llm'&&<p className="pilot-muted">근거 연결 완료 · LLM 종합 대기</p>}
 <div className="pilot-wiki-actions"><button className="pilot-disclosure" aria-expanded={open} onClick={()=>setOpen(v=>!v)}>{open?'Wiki 닫기':'Wiki 열기'} · 논문 {wiki.paper_count}편 · 주장 {wiki.claim_count}개<ChevronRight size={14}/></button>{wiki.path&&<a href={'/api/pilot/wiki-files/'+wiki.id+'/markdown'} target="_blank" rel="noreferrer">Markdown 원본<ArrowUpRight size={13}/></a>}</div>
 {open&&<div>{error?<p role="alert">{error}</p>:data?<>{data.markdown&&<div className="pilot-wiki-document"><WikiMarkdown text={data.markdown} path={data.path}/></div>}{!!data.history?.length&&<details className="pilot-wiki-history"><summary>변경 이력 · 최근 {data.history.length}회</summary>{data.history.map(h=><p key={h.revision}><a href={'/api/pilot/wiki-files/'+wiki.id+'/markdown?revision='+h.revision} target="_blank" rel="noreferrer">v{h.revision}</a> · {time(h.created_at)} · {h.reason}</p>)}</details>}<h3>연결된 근거</h3>{data.claims.map(c=><ClaimCard key={c.id} claim={c} onPaper={onPaper}/>)}{data.total_claims>3&&<Pager page={page} total={data.total_claims} size={3} onPage={setPage} label="Wiki 근거"/>}</>:<p className="pilot-muted">연결된 근거를 읽고 있습니다…</p>}</div>}</article>;
}
export function StatusView({status,onPaper,onChanged}:{status:CorpusStatus|null;onPaper:(id:string)=>void;onChanged:()=>void}){
 return <><SourceReviews status={status} onChanged={onChanged}/>
 {status?.unresolved_titles.map(p=><div className="pilot-issue" key={p.id}><FileText size={17}/><button onClick={()=>onPaper(p.id)}>{p.title}<small>제목 확인 필요</small></button></div>)}
 <div className="pilot-section-title"><h2>지식 반영 이력</h2><span>최근 10회</span></div>
 {status?.changes.map(c=><article className="pilot-change" key={c.id}><span className="pilot-change-dot"/><div><strong>{time(c.created_at)} KST</strong><p>논문 {c.papers}편 · 주장 {c.claims}개 · 수치 {c.measurements}개 · Wiki {c.wiki_pages}개 추가</p><small>원문 검토 및 지식 추가</small></div></article>)}
 </>;
}
export function GuideView(){return <>
 <div className="pilot-guide-grid"><article><span>01</span><h2>질문하기</h2><p>질문을 입력하고 ‘질문하기’를 누르면 담당 논문을 바탕으로 전문가를 자동으로 선택해 답변합니다. 원하는 전문가를 직접 고를 수도 있습니다.</p></article><article><span>02</span><h2>사실과 조건 함께 읽기</h2><p>각 claim에는 연결된 수치와 실험 조건, 측정·시뮬레이션·추정 구분이 있습니다. 근거를 펼치면 원문 PDF로 이동합니다.</p></article><article><span>03</span><h2>Wiki에서 지식 탐색</h2><p>전문가별 주제를 검색하고 사용한 근거를 펼쳐 보세요. 주요 항목만 표시하며 나머지는 페이지를 넘겨 확인합니다.</p></article></div>
 </>;}

