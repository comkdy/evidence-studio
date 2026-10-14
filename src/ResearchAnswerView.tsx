import type { AnswerWiki, SearchResult } from './pilotData';
import { answerLimitations, researchModeLabels } from './researchAnswer';
import { WikiMarkdown } from './WikiMarkdown';

function WikiReferences({wikis}:{wikis:AnswerWiki[]}){
 if(!wikis.length)return null;
 return <details className="pilot-answer-wikis"><summary>참고한 Wiki {wikis.length}개</summary>{wikis.map(w=><p key={w.id}><a href={'/api/pilot/wiki-files/'+encodeURIComponent(w.id)+'/markdown?revision='+w.revision} target="_blank" rel="noreferrer">{w.title} · v{w.revision}</a></p>)}</details>;
}

export function ResearchAnswerView({result}:{result:SearchResult}){
 const limitations=answerLimitations(result);
 return <>
  {result.synthesis&&<article className="pilot-synthesis" aria-label="종합 답변">
   <div className="pilot-section-title"><h2>{result.synthesis.mode==='llm'?'종합 판단':'검색한 근거'}</h2><span>{result.synthesis.mode==='llm'?'근거 기반 답변':'종합 답변 미생성'}</span></div>
   {result.research&&<p className="pilot-research-meta">{researchModeLabels[result.research.mode]} 질의 · 검색한 논문 {result.research.paper_count}편</p>}
   <WikiMarkdown text={result.synthesis.markdown}/>
   {result.synthesis.notice&&<p className="pilot-answer-notice">{result.synthesis.notice}</p>}
   <WikiReferences wikis={result.synthesis.wikis}/>
  </article>}
  {!!limitations.length&&<aside className="pilot-answer-limitations" aria-label="답변의 범위와 한계"><h3>답변의 범위와 한계</h3><ul>{limitations.map(item=><li key={item}>{item}</li>)}</ul></aside>}
  {!!result.answers?.length&&<details className="pilot-expert-perspectives" open={!result.synthesis}>
   <summary>전문가별 관점 {result.answers.length}개</summary>
   {result.answers.map(a=><article className="pilot-expert-answer" key={a.expert_id}><div className="pilot-labels"><span>{a.expert_name}</span><small>{a.mode==='llm'?'근거 기반 답변':a.mode==='pending'?'지식 구성 중':'검색한 근거 · 답변 미생성'}</small></div><WikiMarkdown text={a.markdown}/>{a.notice&&<p className="pilot-answer-notice">{a.notice}</p>}<WikiReferences wikis={a.wikis}/></article>)}
  </details>}
 </>;
}
