import type { ProgressEvent } from './researchConversation.ts';
import { readEventStream } from './researchConversation.ts';

export type ReportStatement={text:string;kind:'finding'|'synthesis'|'hypothesis'|'proposal';evidence_ids:string[];source_spans:{quote:string;page_number:number;document_id:string}[]};
export type SourceSpan={quote:string;page_number:number;document_id?:string};
export type ReportSource={kind:'claim'|'source_text';paper_title:string;answer_run_id:string;paper_id?:string;text?:string;page_number?:number;document_id?:string;claim?:{paper_id:string;summary:string;sources:SourceSpan[]}};
export type ReportSection={id:string;title:string;layout:'prose'|'bullets'|'table';statements:ReportStatement[];sources:Record<string,ReportSource>};
export type Report={id:string;revision:number;current_revision:number;updated_at:string;document:{title:string;purpose:string;sections:ReportSection[]};instruction:string;section_id:string;model:string;usage:{edit_calls?:number;elapsed_ms?:number;research_run_id?:string|null;tokens?:{total_tokens?:number}[]}};
export type ReportSummary=Pick<Report,'id'|'revision'|'updated_at'>&{title:string};
export type ReportVersion={revision:number;instruction:string;section_id:string;model:string;created_at:string};
export const statementLabels={finding:'논문 근거',synthesis:'종합 해석',hypothesis:'검증 전 가설',proposal:'제안 실험'};
export const layoutLabels={prose:'본문',bullets:'핵심 항목',table:'비교 표'};
export function reportFocus(report:Report|null,sectionId:string){
 if(!report||report.revision!==report.current_revision||!report.document.sections.some(s=>s.id===sectionId))return {};
 return {report_id:report.id,report_section_id:sectionId,report_revision:report.revision};
}
export function readReportStream(response:Response,signal:AbortSignal,onEvent:(event:ProgressEvent)=>void){
 return readEventStream<Report>(response,signal,event=>{if(event.type==='progress')onEvent(event);},'report_result',value=>
  !!value&&typeof value==='object'&&'id' in value&&typeof value.id==='string'&&'revision' in value&&typeof value.revision==='number'&&'document' in value&&!!value.document&&typeof value.document==='object'&&'sections' in value.document&&Array.isArray(value.document.sections));
}
