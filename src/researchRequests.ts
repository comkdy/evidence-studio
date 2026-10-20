import type { ConversationTurn, ResearchRequest, ResearchRequestSummary, SavedSearch } from './pilotData';

export function requestPending(status:ResearchRequest['status']){return status==='queued'||status==='running';}

/** A navigation detaches from durable work; only the report editing stream stops. */
export function detachConversationTurns(turns:ConversationTurn[]):ConversationTurn[]{
 return turns.map(turn=>turn.status==='pending'&&!turn.durable&&!turn.request?{...turn,status:'stopped',message:'화면을 이동하여 보고서 편집 연결을 중지했습니다. 보고서의 최신 버전을 확인해 주세요.'}:turn);
}

/** Older polling responses must never undo a terminal result or a visibility change. */
export function applyResearchRequest(turn:ConversationTurn,request:ResearchRequest):ConversationTurn{
 if(turn.request?.id===request.id&&((turn.request.updated_at>request.updated_at)||(!requestPending(turn.request.status)&&requestPending(request.status))))return turn;
 const {id,is_owner,status,phase,visibility,updated_at,cache_hit,learning_eligible,report_id,report_section_id,report_revision}=request;
 const turnStatus=requestPending(status)?'pending':status==='completed'?'completed':status==='cancelled'?'stopped':'error';
 // The API advances updated_at for result/state changes. Avoid cloning and writing
 // the same potentially large evidence packet to localStorage on every poll.
 if(turn.request?.id===id&&turn.request.updated_at===updated_at&&turn.request.status===status&&turn.request.phase===phase&&turn.request.visibility===visibility&&turn.status===turnStatus&&turn.result)return turn;
 return {...turn,durable:true,question:request.question,expertIds:request.expert_ids,expertNames:request.expert_names,
  status:turnStatus,
  result:request.result??request.local_evidence,message:request.error||(status==='cancelled'?'이 질문의 준비 작업을 중지했습니다.':undefined),
  historyRunIds:is_owner===false?[]:(request.history_run_ids??turn.historyRunIds??[]),
  request:{id,is_owner,status,phase,visibility,updated_at,cache_hit,learning_eligible,report_id,report_section_id,report_revision}};
}

export function requestTurn(request:ResearchRequest):ConversationTurn{
 return applyResearchRequest({id:request.id,question:request.question,expertIds:[],expertNames:[],createdAt:request.created_at,status:'pending'},request);
}

/** Access withdrawal hides the cached shared result without cancelling its work. */
export function unavailableRequestTurn(turn:ConversationTurn):ConversationTurn{
 return {...turn,result:undefined,status:'error',message:'질문이 없거나 공유 상태가 변경되어 현재 열 수 없습니다.'};
}

/** Server and browser histories overlap: count each server request only once. */
export function localOnlyQuestionCount(records:{deleted:boolean;turns?:ConversationTurn[]}[]){
 return records.filter(record=>!record.deleted).reduce((total,record)=>total+(record.turns?.filter(turn=>!turn.request&&!turn.durable).length??1),0);
}

export type RecentResearchEntry={kind:'conversation';record:SavedSearch}|{kind:'request';request:ResearchRequestSummary};
/** Prefer the local conversation grouping when it already contains a server request. */
export function recentResearchEntries(records:SavedSearch[],requests:ResearchRequestSummary[],limit=5):RecentResearchEntry[]{
 const conversations=records.filter(record=>!record.deleted),linked=new Set(conversations.flatMap(record=>record.turns?.flatMap(turn=>turn.request?[turn.request.id]:[])??[]));
 return [...conversations.map(record=>({entry:{kind:'conversation',record} as RecentResearchEntry,time:record.turns?.at(-1)?.createdAt??record.createdAt})),
  ...requests.filter(request=>!linked.has(request.id)).map(request=>({entry:{kind:'request',request} as RecentResearchEntry,time:request.created_at}))]
  .sort((a,b)=>b.time.localeCompare(a.time)).slice(0,limit).map(item=>item.entry);
}
