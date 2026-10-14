export type UsageCounts={calls:number;completed:number;failed:number;running:number;interrupted:number;waiting:number;not_sent:number;model_elapsed_ms:number;input_tokens:number|null;output_tokens:number|null;total_tokens:number|null;input_reports:number;output_reports:number;total_reports:number};
export type ModelUsage=UsageCounts&{key:string;model:string;role:'default'|'light';phases:string[]};
export type WikiUsage={available:false;models:ModelUsage[]}|(UsageCounts&{available:true;models:ModelUsage[];attempts:number;interrupted_attempts:number;started_at:string;elapsed_ms:number});
export type WikiJob={id:number;scope:'all'|'common'|'expert';label:string;state:'queued'|'running'|'done'|'failed';created_at:string;finished_at:string|null;error?:string;usage:WikiUsage};

export function tokenLabel(value:number|null,reported:number,calls:number){
 if(calls===0)return '0';
 if(value===null)return '미제공';
 return value.toLocaleString('ko-KR')+(reported<calls?' (일부)':'');
}
export function durationLabel(milliseconds:number){
 if(!Number.isFinite(milliseconds)||milliseconds<0)return '기록 없음';
 if(milliseconds===0)return '0초';
 if(milliseconds<1000)return '1초 미만';
 const seconds=Math.floor(milliseconds/1000),hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60);
 return [hours?`${hours}시간`:'',minutes?`${minutes}분`:'',`${seconds%60}초`].filter(Boolean).join(' ');
}
export function callLabel(usage:UsageCounts){
 return `${usage.calls}회`+[
  usage.running?`진행 ${usage.running}`:'',usage.failed?`실패 ${usage.failed}`:'',
  usage.interrupted?`중단 ${usage.interrupted}`:'',usage.waiting?`호출 대기 ${usage.waiting}`:'',
  usage.not_sent?`미전송 ${usage.not_sent}`:'',
 ].filter(Boolean).map(s=>' · '+s).join('');
}
