import type { PaperKnowledge as Knowledge } from './pilotData';
import { WikiCard } from './PilotViews';
import './PaperKnowledge.css';

const states:Record<string,string>={ready:'원문 근거 준비됨',partial:'일부 구간 준비됨',pending:'원문 근거 준비 대기'};
const reviews:Record<string,string>={model_reviewed_human_pending:'모델 검토 완료 · 사람 검토 대기',curated_evidence:'기존 구조화 근거 활용',pending:'검토 대기'};
export function PaperKnowledge({knowledge,onExpert,onPaper,revision}:{knowledge?:Knowledge;onExpert:(id:string)=>void;onPaper:(id:string)=>void;revision:number}){
 if(!knowledge)return null;
 const {coverage,common_wikis,experts}=knowledge;
 return <section className="paper-knowledge" aria-label="논문의 지식 연결">
  <header><h2>이 논문의 원문 근거 준비</h2><span>{states[coverage.status]??'원문 상태 확인 필요'}</span></header>
  <p>원문 분석 구간 <strong>{coverage.ready_chunks} / {coverage.total_chunks}</strong> 준비 · {reviews[coverage.review_state]??'검토 상태 확인 필요'}</p>
  <progress aria-label="이 논문의 원문 분석 구간 준비" value={coverage.ready_chunks} max={Math.max(1,coverage.total_chunks)}/>
  <p className="paper-knowledge-note">구간 전체는 현재 추출 가능한 원문 범위입니다. 이미지·표 등 추출되지 않은 내용은 별도 확인이 필요합니다. 원문 구간 분석과 아래의 구조화된 주장·수치 목록은 별도로 준비됩니다.</p>
  {!!experts.length&&<div className="paper-expert-links"><strong>담당 전문가 지식</strong>{experts.map(expert=><button key={expert.id} className="secondary" onClick={()=>onExpert(expert.id)}>{expert.name}{expert.enabled===0||expert.enabled===false?' · 사용 중지':''}의 지식 보기</button>)}</div>}
  <div className="paper-common-wikis"><h3>이 논문이 연결된 공통 Wiki {common_wikis.length}개</h3>{common_wikis.map(wiki=><WikiCard key={wiki.id} wiki={wiki} refreshKey={revision} onPaper={onPaper}/>)}{!common_wikis.length&&<p>현재 원문과 연결되어 사용할 수 있는 공통 Wiki가 없습니다.</p>}</div>
 </section>;
}
