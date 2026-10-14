import { useState } from 'react';
import { ArrowLeft, ArrowRight, ArrowUpRight, BookOpen, Check, Clock3, History, MessageSquare, RefreshCw, Search, Sparkles } from 'lucide-react';
import { evidenceData, getAnswerClaims, getSpan, kindLabels } from './evidence';
import { SourceQuote } from './PaperEvidence';
import { KnowledgeList } from './KnowledgeList';
import type { PaperTab } from './PaperEvidence';
import type { Answer, Paper } from './types';
import { sharedQuestionCount, wikiChanges, wikiTime, wikiTopics } from './wiki';
import type { WikiBlock, WikiPage } from './wiki';
import './ExpertWiki.css';

export function ExpertWiki({pages,papers,history,initialPageId,onPaper,onReview}:{pages:WikiPage[];papers:Paper[];history:Answer[];initialPageId?:string;onPaper:(paper:Paper,tab?:PaperTab)=>void;onReview:(id:string)=>void}) {
  const [selectedId,setSelectedId]=useState(initialPageId||pages[0]?.id||'');
  const [mode,setMode]=useState<'article'|'history'>('article');
  const [version,setVersion]=useState<number|null>(null);
  const [query,setQuery]=useState('');
  const page=pages.find(item=>item.id===selectedId)||pages[0];
  if(!page)return <div className="knowledge-empty"><BookOpen size={24}/><h3>Wiki에 연결할 지식을 기다리고 있습니다.</h3><p>담당 자료의 근거가 준비되면 주제별 핵심 내용이 이곳에 나타납니다.</p></div>;
  const latest=page.revisions.at(-1)!;
  const revision=page.revisions.find(item=>item.version===version)||latest;
  const old=revision.version!==latest.version;
  const allowed=revision.paperIds.every(id=>latest.paperIds.includes(id));
  const previous=page.revisions.find(item=>item.version===revision.version-1);
  const changes=wikiChanges(revision,previous);
  const count=sharedQuestionCount(page,history);
  const reviewed=(page.reviewedCount||0)>=count;
  const topic=wikiTopics.find(item=>item.id===page.topicId)!;
  const filteredPages=pages.filter(item=>(item.title+' '+item.revisions.at(-1)!.blocks.map(block=>block.title+' '+block.text).join(' ')).toLowerCase().includes(query.toLowerCase().trim()));
  const selectPage=(id:string)=>{setSelectedId(id);setVersion(null);setMode('article');};
  const source=(id:string)=>papers.find(item=>item.id===id);
  const citation=(id:string)=>{const paper=source(id);return paper?<button className="wiki-citation" onClick={()=>onPaper(paper,getAnswerClaims(paper.id).length?'claims':'overview')} aria-label={paper.title+' 근거 보기'}><BookOpen size={12}/>{paper.title}<ArrowUpRight size={12}/></button>:null;};
  const block=(item:WikiBlock,highlight=false)=>{
    const claims=item.claimIds.flatMap(id=>evidenceData.claims.find(claim=>claim.id===id)||[]);
    return <section className={'wiki-block '+(highlight?'wiki-added':'')} key={item.id}>
      <h4>{item.title}</h4><p>{item.text}</p>
      {!!claims.length&&<details className="wiki-evidence-details"><summary>근거 확인 · 논문 {item.paperIds.length}편 · 주장 {claims.length}개 · 수치 {item.measurementIds.length}개</summary>
        <p className="wiki-evidence-caption">위 설명에 사용한 논문별 원문입니다. 주장 유형은 각 출처의 성격을 나타냅니다.</p>
        <KnowledgeList key={item.id+':'+revision.version} items={claims} size={2} label={item.title+' 근거'} itemKey={claim=>claim.id}>{claim=>{
          const numbers=evidenceData.measurements.filter(measure=>item.measurementIds.includes(measure.id)&&claim.measurementIds.includes(measure.id)&&measure.paperId===claim.paperId);
          const spans=[...new Set([...claim.evidence.map(link=>link.spanId),...numbers.flatMap(measure=>[...measure.evidenceIds,...measure.conditions.flatMap(condition=>condition.evidenceIds)])])];
          return <article className="wiki-source-claim"><div className="wiki-source-kind"><span className={'claim-kind '+claim.kind}>{kindLabels[claim.kind]}</span><small>{claim.certainty}</small></div>
            <p>{claim.summary}</p><small className="wiki-claim-scope">적용 범위 · {claim.scope}</small>
            {!claim.hasDirectSupport&&<p className="wiki-source-caution">이 설명을 직접 검증한 실험 근거는 연결되어 있지 않습니다.</p>}
            {numbers.map(measure=><div className="wiki-measure" key={measure.id}><strong>{measure.label} · {measure.rawValue}</strong><small>{measure.sample}</small><p>{measure.conditions.map(condition=>condition.name+' '+(condition.rawValue??'미기재')).join(' · ')}</p></div>)}
            {spans.map(id=><SourceQuote key={id} span={getSpan(id)}/>)}{citation(claim.paperId)}
          </article>;
        }}</KnowledgeList>
      </details>}
      {!claims.length&&<><small className="wiki-source-caution">확인된 근거가 준비되면 지식에 반영됩니다.</small><div className="wiki-citations">{item.paperIds.slice(0,2).map(id=><span key={id}>{citation(id)}</span>)}</div></>}
    </section>;
  };
  return <div className="wiki-browser">
    <nav className="wiki-page-list" aria-label="Wiki 페이지"><div><BookOpen size={15}/><strong>전문가 Wiki</strong><span>{pages.length}</span></div>
      <label className="wiki-search"><Search size={14}/><input aria-label="Wiki 찾기" placeholder="주제·내용 찾기" value={query} onChange={event=>setQuery(event.target.value)}/></label>
      <KnowledgeList key={query} items={filteredPages} size={5} label="Wiki 목록" itemKey={item=>item.id}>{item=>{const current=item.revisions.at(-1)!;return <button aria-pressed={page.id===item.id} onClick={()=>selectPage(item.id)}><strong>{item.title}</strong><small>v{current.version} · 근거 {current.paperIds.length}편</small>{current.version>1&&<span className="wiki-revised">갱신됨</span>}</button>;}}</KnowledgeList>
      {!filteredPages.length&&<p>일치하는 Wiki가 없습니다.</p>}<p>주제를 선택해 핵심 내용과 근거를 읽어보세요.</p>
    </nav>
    <div className="wiki-document">
      <header className="wiki-document-heading"><div className="eyebrow">EXPERT WIKI <span>예시 문서</span></div><h2>{page.title}</h2><p className="wiki-reader-intro">담당 자료에서 확인한 핵심 내용과 아직 확인되지 않은 내용을 함께 정리했습니다.</p><div className="wiki-document-meta"><span><Clock3 size={13}/>마지막 갱신 {wikiTime(latest.at)}</span><span>v{latest.version} · 담당 자료 {latest.paperIds.length}편</span></div></header>
      <div className="wiki-view-tabs" role="tablist" aria-label="Wiki 문서 보기"><button role="tab" id="wiki-article-tab" aria-controls="wiki-content" aria-selected={mode==='article'} onClick={()=>setMode('article')}><BookOpen size={14}/>내용과 근거</button><button role="tab" id="wiki-history-tab" aria-controls="wiki-content" aria-selected={mode==='history'} onClick={()=>setMode('history')}><History size={14}/>변경 이력 {page.revisions.length}</button></div>
      <div id="wiki-content" role="tabpanel" aria-labelledby={mode==='article'?'wiki-article-tab':'wiki-history-tab'}>
      {mode==='article'?<>
        {old&&<div className="wiki-old-version"><History size={14}/>v{revision.version}의 이전 내용 · {wikiTime(revision.at)}<button onClick={()=>setVersion(null)}>최신 내용으로 돌아가기<ArrowRight size={12}/></button></div>}
        {!allowed?<div className="knowledge-empty">이 버전에는 현재 담당 논문에서 제외된 근거가 있습니다. 최신 버전에서 현재 유효한 지식을 확인하세요.</div>:<>
          {!revision.blocks.some(item=>item.kind==='knowledge')&&<div className="knowledge-empty">아직 핵심 지식으로 정리할 확인된 근거가 없습니다.</div>}
          {(['knowledge','gap','faq','pending'] as const).map(kind=>{
            const blocks=revision.blocks.filter(item=>item.kind===kind);
            const label=({knowledge:'핵심 지식',gap:'아직 확인되지 않은 내용',faq:'자주 묻는 질문',pending:'근거 확인을 기다리는 자료'})[kind];
            return blocks.length?<section className={'wiki-section wiki-section-'+kind} key={kind}><h3>{label}<span className="wiki-section-count">{blocks.length}</span></h3><KnowledgeList key={page.id+':'+revision.version+':'+kind} items={blocks} size={kind==='knowledge'?3:2} label={label} itemKey={item=>item.id}>{item=>block(item,!old&&latest.version>1&&changes.added.some(added=>added.id===item.id))}</KnowledgeList></section>:null;
          })}
          <details className="wiki-source-list"><summary>근거 논문 전체 보기 <span>{revision.paperIds.length}편</span></summary><KnowledgeList key={page.id+':'+revision.version} items={revision.paperIds.flatMap(id=>source(id)||[])} size={5} label="근거 논문" itemKey={paper=>paper.id}>{paper=><button onClick={()=>onPaper(paper)}><BookOpen size={17}/><span><strong>{paper.title}</strong><small>{paper.year} · {paper.topic} · {getAnswerClaims(paper.id).length?'주장·원문 근거 확인 가능':'근거 확인 대기'}</small></span><ArrowUpRight size={15}/></button>}</KnowledgeList></details>
        </>}
        {!old&&<details className="wiki-maintenance"><summary>관심 질문과 Wiki 점검 <span>공유 질문 {count}회 · 가상 집계</span></summary><section className="wiki-question-signal"><div><div className="wiki-signal-label"><MessageSquare size={14}/>최근 7일 관심 질문</div><h3>{topic.question}</h3><p>{reviewed?'새로 확인할 질문이 없습니다.':page.faqEnabled?'추가 질문이 있어 기존 내용을 다시 점검합니다.':'담당 근거를 확인해 자주 묻는 질문에 대한 설명을 보강합니다.'}</p>{page.lastCheckedAt&&<small>최근 점검 {wikiTime(page.lastCheckedAt)}</small>}</div><button className={reviewed?'secondary':'primary'} disabled={reviewed} onClick={()=>onReview(page.id)}>{reviewed?<Check size={14}/>:<RefreshCw size={14}/>} {reviewed?(page.faqEnabled?'점검 완료':'근거 대기'):page.faqEnabled?'Wiki 다시 점검':'Wiki 점검·FAQ 보강'}</button></section></details>}
      </>:<div className="wiki-history"><p className="wiki-history-caption">버전을 선택하면 추가·수정된 내용과 당시 근거를 확인할 수 있습니다.</p>
        <div className="wiki-revision-list"><KnowledgeList key={page.id} items={[...page.revisions].reverse()} size={5} label="변경 이력" itemKey={item=>String(item.version)}>{item=><button aria-pressed={revision.version===item.version} onClick={()=>setVersion(item.version===latest.version?null:item.version)}><span className="wiki-version">v{item.version}</span><span><strong>{item.reason}</strong><small>{wikiTime(item.at)} · 근거 {item.paperIds.length}편</small></span>{item.version===latest.version&&<span className="tag blue">최신</span>}</button>}</KnowledgeList></div>
        <div className="wiki-change-heading"><h3>v{revision.version} {previous?'← v'+previous.version:'· 첫 버전'}</h3><button className="quiet-button" onClick={()=>setMode('article')}>이 버전 내용 보기<ArrowRight size={13}/></button></div>
        {!allowed?<div className="knowledge-empty">현재 담당 논문에서 제외된 근거가 포함되어 있어 이 버전의 상세 내용은 표시하지 않습니다.</div>:<>
          <p className="wiki-change-summary">추가 {changes.added.length}개 · 수정 {changes.edited.length}개 · 제외 {changes.removed.length}개</p>
          {!!revision.addedPaperIds.length&&<div className="wiki-change-sources"><strong>추가된 근거</strong><KnowledgeList key={revision.version} items={revision.addedPaperIds} size={3} label="추가 근거" itemKey={id=>id}>{citation}</KnowledgeList></div>}
          <KnowledgeList key={'added-'+revision.version} items={changes.added} size={3} label="추가 내용" itemKey={item=>item.id}>{item=><div className="wiki-change-item"><span className="wiki-change-label">추가</span>{block(item,true)}</div>}</KnowledgeList>
          <KnowledgeList key={'edited-'+revision.version} items={changes.edited} size={3} label="수정 내용" itemKey={item=>item.after.id}>{item=><div className="wiki-change-item"><span className="wiki-change-label">수정</span><div className="wiki-before"><small>이전</small><p>{item.before.paperIds.every(id=>latest.paperIds.includes(id))?item.before.title+' — '+item.before.text:'현재 담당 범위에서 제외된 근거가 포함되어 있습니다.'}</p></div><small className="wiki-after-label">이후</small>{block(item.after,true)}</div>}</KnowledgeList>
          {!!changes.removed.length&&<p className="wiki-history-caption">담당 범위에서 제외된 내용 {changes.removed.length}개를 제거했습니다.</p>}
        </>}
      </div>}
      </div>
      <footer className="wiki-document-footer"><Sparkles size={13}/>논문의 보고 범위와 미확인 부분을 함께 읽어 주세요. 모든 내용은 화면 확인용 가상 문헌에 기반합니다.</footer>
      {mode==='history'&&<button className="quiet-button wiki-back" onClick={()=>{setVersion(null);setMode('article');}}><ArrowLeft size={13}/>최신 내용으로 돌아가기</button>}
    </div>
  </div>;
}
