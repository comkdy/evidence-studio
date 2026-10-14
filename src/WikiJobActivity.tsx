import { useState } from 'react';
import { time } from './pilotData';
import { tokenLabel, durationLabel, callLabel } from './wikiUsage';
import type { WikiJob } from './wikiUsage';

const states={queued:'대기',running:'처리 중',done:'처리 종료',failed:'문제 발생'};
const phases:Record<string,string>={source:'원천 Wiki',source_part:'원문 묶음',topic:'공통 주제',topic_part:'중간 종합',specialization:'전문가 특화',other:'Wiki 처리'};

export function WikiJobActivity({jobs}:{jobs:WikiJob[]}){
 const [expanded,setExpanded]=useState(false);
 return <section className="wiki-activity" aria-labelledby="wiki-activity-title">
  <div className="wiki-activity-heading"><h3 id="wiki-activity-title">Wiki 작업 내역</h3><span>모델 · 호출 · 토큰 · 소요 시간</span></div>
  <p className="wiki-activity-caption">실제 LLM 호출을 작업별로 집계합니다. 진행 중에는 5초마다 갱신하며, 토큰은 API가 반환한 사용량입니다.</p>
  {!jobs.length&&<p className="pilot-muted">아직 Wiki 작업이 없습니다.</p>}
  {(expanded?jobs:jobs.slice(0,3)).map(job=>{
   const u=job.usage;
   return <article className={'wiki-usage-job '+job.state} key={job.id} aria-label={`Wiki 작업 #${job.id}`}>
    <header><div><strong>{job.label}</strong><span>#{job.id} · {time(job.created_at)}</span></div><span className="wiki-job-state">{states[job.state]}</span></header>
    {job.error&&<p className="wiki-job-error" role="status">{job.error}</p>}
    {u.available?<>
     <p className="wiki-model-list">{u.models.length?u.models.map(model=><span key={model.key}><b>{model.model}</b><small>{model.role==='light'?'경량':'기본'}</small></span>):'LLM 호출 없음'}</p>
     <dl className="wiki-usage-totals">
      <div><dt>호출 횟수</dt><dd>{callLabel(u)}</dd></div>
      <div><dt>입력 토큰</dt><dd>{tokenLabel(u.input_tokens,u.input_reports,u.calls)}</dd></div>
      <div><dt>출력 토큰</dt><dd>{tokenLabel(u.output_tokens,u.output_reports,u.calls)}</dd></div>
      <div><dt>전체 토큰</dt><dd>{tokenLabel(u.total_tokens,u.total_reports,u.calls)}</dd></div>
      <div><dt>작업 소요 시간</dt><dd>{durationLabel(u.elapsed_ms)}{job.state==='running'?' · 진행 중':''}</dd></div>
     </dl>
     {u.calls>0&&(u.input_reports<u.calls||u.output_reports<u.calls||u.total_reports<u.calls)&&<p className="wiki-usage-note">토큰 확인: 입력 {u.input_reports}/{u.calls}회 · 출력 {u.output_reports}/{u.calls}회 · 전체 {u.total_reports}/{u.calls}회. API가 반환한 항목만 합산합니다.</p>}
     {u.interrupted_attempts>0&&<p className="wiki-usage-note">중단 후 재개한 작업입니다. 기록된 {u.attempts}회 실행을 합산했으며, 중단 당시 확인하지 못한 사용량은 포함되지 않을 수 있습니다.</p>}
     {u.models.length>0&&<details className="wiki-model-details"><summary>모델별 사용량</summary><div className="wiki-usage-table-wrap"><table><thead><tr><th>모델 · 수행 작업</th><th>호출</th><th>입력 토큰</th><th>출력 토큰</th><th>전체 토큰</th><th>모델 응답 시간</th></tr></thead><tbody>{u.models.map(m=><tr key={m.key}><td><strong>{m.model}</strong><small>{m.phases.map(p=>phases[p]||p).join(' · ')}</small></td><td>{callLabel(m)}</td><td>{tokenLabel(m.input_tokens,m.input_reports,m.calls)}</td><td>{tokenLabel(m.output_tokens,m.output_reports,m.calls)}</td><td>{tokenLabel(m.total_tokens,m.total_reports,m.calls)}</td><td>{durationLabel(m.model_elapsed_ms)}</td></tr>)}</tbody></table></div><p className="wiki-usage-note">작업 시간은 최초 대기 시간을 제외한 실행 시간입니다. 모델 응답 시간은 전송 후 응답·오류까지의 누적 시간이며, embedding·reranker 사용량은 제외합니다.</p></details>}
    </>:<p className="wiki-usage-note">{job.state==='queued'?'실행을 시작하면 사용량을 기록합니다.':'사용량 기록 없음 · 기능 추가 이전 작업은 사용량을 소급 계산하지 않습니다.'}</p>}
   </article>;
  })}
  {jobs.length>3&&<button className="wiki-usage-expand" type="button" onClick={()=>setExpanded(!expanded)}>{expanded?'최근 3개만 보기':`작업 ${jobs.length}개 모두 보기`}</button>}
 </section>;
}
