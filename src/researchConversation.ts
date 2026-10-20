import type { ConversationTurn, SavedSearch, SearchResult } from './pilotData';

export type ProgressEvent={type:'progress';stage:string;message:string};
export type ExpertEvent={type:'experts';ids:string[];names:string[]};
export type ResearchEvent=ProgressEvent|ExpertEvent;
export function conversationTurns(record:SavedSearch):ConversationTurn[]{
 if(Array.isArray(record.turns)&&record.turns.length){
  const valid=record.turns.filter(t=>t&&typeof t.id==='string'&&typeof t.question==='string'&&typeof t.createdAt==='string'&&
   Array.isArray(t.expertIds)&&t.expertIds.every(x=>typeof x==='string')&&Array.isArray(t.expertNames)&&t.expertNames.every(x=>typeof x==='string')&&
   ['pending','completed','stopped','error'].includes(t.status)&&(!t.result||(Array.isArray(t.result.claims)&&Array.isArray(t.result.hits)))&&
   (t.status!=='completed'||t.result||(t.reportChange&&typeof t.reportChange.reportId==='string'&&typeof t.reportChange.sectionTitle==='string'&&typeof t.reportChange.revision==='number')));
  if(valid.length)return structuredClone(valid).map(t=>t.status==='pending'&&!t.request?{...t,status:'stopped',message:t.durable?'질문 등록 중 화면이 닫혔습니다. 서버의 내 질문 목록에서 처리 상태를 확인해 주세요.':'페이지를 닫아 이전 답변이 중단되었습니다. 다시 질문할 수 있습니다.'}:t);
 }
 return [{id:record.id,question:record.question,expertIds:record.expertIds,expertNames:record.expertNames,createdAt:record.createdAt,status:'completed',result:structuredClone(record.result)}];
}
export function contextRunIds(turns:ConversationTurn[]):string[]{
 return [...new Set(turns.filter(t=>t.status==='completed'&&t.request?.is_owner!==false).flatMap(t=>[...(t.historyRunIds??[]),...(t.result?.run_id?[t.result.run_id]:[])]))].slice(-6);
}
export function saveConversation(id:string,turns:ConversationTurn[],visibility:SavedSearch['visibility']):SavedSearch{
 const last=[...turns].reverse().find(t=>t.result), first=turns[0];
 const requests=turns.flatMap(turn=>turn.request?[turn.request]:[]);
 if(requests.length)visibility=requests.some(request=>request.visibility==='team')?'team':'private';
 return {id,question:first.question,createdAt:first.createdAt,expertIds:last?.expertIds??[],expertNames:last?.expertNames??[],visibility,deleted:false,mode:'deep',turns:structuredClone(turns),
  result:last?.result??{claims:[],hits:[],terms:[],note:'아직 완료된 답변이 없습니다.',total_claims:0}};
}
export function readResearchStream(response:Response,signal:AbortSignal,onEvent:(event:ResearchEvent)=>void):Promise<SearchResult>{
 return readEventStream<SearchResult>(response,signal,onEvent,'result',value=>!!value&&typeof value==='object'&&'claims' in value&&Array.isArray(value.claims)&&'hits' in value&&Array.isArray(value.hits));
}
export async function readEventStream<T>(response:Response,signal:AbortSignal,onEvent:(event:ResearchEvent)=>void,resultType:string,validate:(value:unknown)=>boolean):Promise<T>{
 if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(typeof data.detail==='string'?data.detail:'답변 요청을 시작하지 못했습니다.');}
 if(!response.body)throw new Error('진행 상황 연결을 열지 못했습니다.');
 const reader=response.body.getReader(),decoder=new TextDecoder();
 let buffer='',result:T|undefined;
 const abort=()=>{void reader.cancel().catch(()=>{});};
 signal.addEventListener('abort',abort,{once:true});
 function line(text:string){
  if(!text.trim())return;
  const value=JSON.parse(text);
  if(value.type==='error')throw new Error(typeof value.message==='string'?value.message:'답변 생성 오류');
  if(value.type==='cancelled')throw new DOMException('중지됨','AbortError');
  if(value.type===resultType){
   if(!validate(value.result))throw new Error('답변 형식을 확인해 주세요.');
   result=value.result;
  }else if(value.type==='progress'&&typeof value.stage==='string'&&typeof value.message==='string')onEvent(value);
  else if(value.type==='experts'&&Array.isArray(value.ids)&&Array.isArray(value.names)&&value.ids.every((x:unknown)=>typeof x==='string')&&value.names.every((x:unknown)=>typeof x==='string'))onEvent(value);
 }
 try{
  while(true){
   signal.throwIfAborted();
   const {value,done}=await reader.read();
   signal.throwIfAborted();
   buffer+=decoder.decode(value,{stream:!done});
   if(buffer.length>8_000_000)throw new Error('답변이 너무 큽니다.');
   let boundary:number;
   while((boundary=buffer.indexOf('\n'))>=0){line(buffer.slice(0,boundary));buffer=buffer.slice(boundary+1);}
   if(done)break;
  }
  if(buffer.trim())line(buffer);
  if(!result)throw new Error('연결이 종료되었습니다. 답변을 다시 요청해 주세요.');
  return result;
 }finally{signal.removeEventListener('abort',abort);void reader.cancel().catch(()=>{});reader.releaseLock();}
}
