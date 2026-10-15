export type Source = {id:string;page_number:number;page_id:string;quote:string;locator:string;document_id?:string};
export type Expert = {id:string;name:string;overview:string;topic_tag:string;paper_count:number;claim_count:number;wiki_count:number;updated_at:string|null;enabled?:number};
export type Measure = {id:string;label:string;raw_value:string;kind:string;sample:string;sources:Source[];conditions:{name:string;raw_value:string|null;source:Source|null}[]};
export type Claim = {id:string;paper_id:string;document_id?:string;title?:string;summary:string;kind:string;scope:string;sources:Source[];measurements:Measure[];experts:{id:string;name:string}[]};
export type AbstractSummary = {text:string;language:'ko';source:'abstract';producer:string;prompt_version:string;created_at:string};
export type Paper = {id:string;title:string;title_source:string;abstract:string;abstract_summary?:AbstractSummary|null;filename:string;document_id:string;page_count:number;status:string;claim_count:number;chunk_count:number;topics:{tag:string;basis:string}[]};
export type Detail = Paper & {claims:Claim[];pages:{id:string;page_number:number;status:string;char_count:number}[]};
export type Wiki = {id:string;expert_id:string;expert_name:string;title:string;body:string;limitation:string;created_at:string;claim_count:number;paper_count:number;markdown?:string;path?:string;revision?:number;status?:string;scope?:string;kind?:string};
export type WikiDetail = Wiki & {claims:Claim[];total_claims:number;history?:{revision:number;producer:string;reason:string;created_at:string}[]};
export type Hit = {id:string;paper_id:string;title:string;text:string;page_number:number;page_end?:number;page_id:string;document_id:string;parent_context?:{text:string;section:string;pages:number[]}};
export type ResearchMode = 'basic'|'deep';
export type AnswerWiki = {id:string;title:string;revision:number;path:string};
export type AnswerSynthesis = {markdown:string;mode:'llm'|'evidence';notice?:string;wikis:AnswerWiki[]};
export type SearchResult = {claims:Claim[];hits:Hit[];terms:string[];note:string;total_claims:number;answers?:{expert_id:string;expert_name:string;markdown:string;mode:string;notice?:string;wikis:AnswerWiki[]}[];synthesis?:AnswerSynthesis;research?:{mode:ResearchMode;queries:string[];limitations:string[];paper_count:number;rounds:number;validation_status:string};run_id?:string};
export type Summary = {counts:Record<string,number>;page_states:{status:string;count:number}[];experts:Expert[];curated_papers:number;updated_at:string|null;can_manage_experts:boolean};
export type Schema = {tables:{name:string;label:string;count:number;columns:{name:string;type:string;pk:number}[];foreign_keys:{from:string;table:string;to:string}[]}[];sql:string;database_file:string};
export type PageIssue={paper_id:string;title:string;document_id:string;page_id:string;page_number:number;status:string;char_count:number;pending_requests:number};
export type CorpusStatus = {issues:PageIssue[];reviewed_pages:PageIssue[];changes:{id:string;created_at:string;papers:number;claims:number;measurements:number;wiki_pages:number}[];unresolved_titles:{id:string;title:string}[]};
export type Recommendation = {experts:{id:string;name:string;overview:string;matching_papers:number}[];method:string};
export type ConversationTurn = {id:string;question:string;expertIds:string[];expertNames:string[];createdAt:string;status:"pending"|"completed"|"stopped"|"error";result?:SearchResult;message?:string;focusTitle?:string;reportChange?:{reportId:string;revision:number;sectionTitle:string;model:string;elapsedMs:number}};
export type SavedSearch = {reportId?:string;turns?:ConversationTurn[];id:string;question:string;expertIds:string[];expertNames:string[];createdAt:string;visibility:'private'|'team';deleted:boolean;mode?:ResearchMode;result:SearchResult};
export type PilotView = 'search'|'library'|'wiki'|'team'|'personal'|'status'|'settings'|'guide'|'users'|'dictionary';
export const viewNames:Record<PilotView,string> = {search:'연구 질문',library:'논문 라이브러리',wiki:'전문가 지식',team:'팀의 질문',personal:'내 페이지',status:'자료 현황',settings:'전문가 설정',guide:'사용 가이드',users:'유저 관리',dictionary:'용어 사전'};
export const kinds:Record<string,string> = {observation:'실험 관측',estimate:'모델·계산 추정',interpretation:'저자 해석',limitation:'적용 한계',measured:'측정',simulated:'시뮬레이션',estimated:'추정'};
export async function get<T>(path:string,signal?:AbortSignal):Promise<T> {
  const response=await fetch('/api/pilot'+path,{signal});
  if(!response.ok)throw new Error(response.status===404?'요청한 자료를 찾을 수 없습니다.':'자료를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.');
  return response.json();
}
export function time(value:string|null){return value?new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}):'아직 기록 없음';}
const viewPaths:Record<PilotView,string> = {search:'ask',library:'library',wiki:'experts',team:'team',personal:'personal',status:'ingest',settings:'settings',guide:'guide',users:'users',dictionary:'dictionary'};
export function viewHash(view:PilotView){return '#'+viewPaths[view];}
export function readView():PilotView {
  const key=location.hash.startsWith('#pilot/')?location.hash.slice(7):location.hash.slice(1);
  const aliases:Record<string,PilotView>={ask:'search',experts:'wiki',ingest:'status',pilot:'library'};
  const value=aliases[key]??key;
  return Object.prototype.hasOwnProperty.call(viewNames,value)?value as PilotView:'search';
}
export function personalKey(employeeId:string,kind:'bookmarks'|'history'){return 'evidence-studio.user.'+employeeId+'.'+kind;}
export function readBookmarks(employeeId:string):string[]{try{const key=personalKey(employeeId,'bookmarks');const raw=localStorage.getItem(key);const v=JSON.parse(raw||'[]');return Array.isArray(v)?v.filter(x=>typeof x==='string'):[];}catch{return [];}}
export function readHistory(employeeId:string):SavedSearch[]{try{const v=JSON.parse(localStorage.getItem(personalKey(employeeId,'history'))||'[]');return Array.isArray(v)?v.filter(x=>x&&typeof x.id==='string'&&typeof x.question==='string'&&Array.isArray(x.expertIds)&&Array.isArray(x.expertNames)&&x.expertNames.every((n:unknown)=>typeof n==='string')&&typeof x.createdAt==='string'&&typeof x.deleted==='boolean'&&['team','private'].includes(x.visibility)&&x.result&&Array.isArray(x.result.claims)&&Array.isArray(x.result.hits)).slice(0,100):[];}catch{return [];}}

