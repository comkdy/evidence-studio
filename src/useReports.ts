import { useEffect, useRef, useState } from 'react';
import { api } from './identity';
import type { Report, ReportSummary, ReportVersion, ReportSection } from './reportData';

export function useReports(){
 const [report,setReport]=useState<Report|null>(null),[list,setList]=useState<ReportSummary[]>([]),[versions,setVersions]=useState<ReportVersion[]>([]);
 const [sectionId,setSectionId]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [open,setOpen]=useState(true),[mobilePane,setMobilePane]=useState<'chat'|'report'>('chat');
 const [mode,setMode]=useState<'question'|'edit'>('question'),[research,setResearch]=useState(false);
 const epoch=useRef(0),reportRef=useRef(report);reportRef.current=report;
 const path=(id:string)=>'/api/pilot/reports/'+encodeURIComponent(id);
 function accept(value:Report){
  reportRef.current=value;setReport(value);setError('');
  setSectionId(id=>value.document.sections.some(s=>s.id===id)?id:'');
  setList(old=>[{id:value.id,title:value.document.title,revision:value.current_revision,updated_at:value.updated_at},...old.filter(x=>x.id!==value.id)]);
  const e=epoch.current;
  void api<ReportVersion[]>(path(value.id)+'/versions').then(v=>{if(e===epoch.current&&reportRef.current?.id===value.id)setVersions(v);}).catch(()=>{});
 }
 async function task(operation:()=>Promise<Report>){
  const e=++epoch.current;setBusy(true);setError('');
  try{const value=await operation();if(e===epoch.current)accept(value);}catch(ex){if(e===epoch.current)setError(ex instanceof Error?ex.message:'보고서를 불러오지 못했습니다.');}
  finally{if(e===epoch.current)setBusy(false);}
 }
 useEffect(()=>{let active=true;api<ReportSummary[]>('/api/pilot/reports').then(v=>{if(active)setList(v);}).catch(()=>{if(active)setError('저장한 보고서 목록을 불러오지 못했습니다.');});return()=>{active=false;epoch.current++;};},[]);
 function clear(){epoch.current++;reportRef.current=null;setReport(null);setSectionId('');setVersions([]);setMode('question');setBusy(false);setError('');}
 function load(id:string,version?:number){if(!id){clear();return;}setSectionId('');setMode('question');setOpen(true);return task(()=>api<Report>(path(id)+(version?'?version='+version:'')));}
 function create(title:string,purpose:string){setSectionId('');setMode('question');return task(()=>api<Report>('/api/pilot/reports',{method:'POST',body:JSON.stringify({title,purpose})}));}
 function select(id:string){setSectionId(id);setMode('question');setMobilePane('chat');}
 function changeSection(action:'add'|'update'|'delete',id:string,title:string,layout:ReportSection['layout']){
  if(!report)return;const current=report;
  return task(()=>api<Report>(path(current.id)+'/sections',{method:'PATCH',body:JSON.stringify({revision:current.revision,action,section_id:id,title,layout})}));
 }
 function restore(){if(!report)return;const current=report;setSectionId('');setMode('question');return task(()=>api<Report>(path(current.id)+'/restore',{method:'POST',body:JSON.stringify({revision:current.current_revision,version:current.revision})}));}
 async function moreVersions(){if(!report||!versions.length)return;try{const next=await api<ReportVersion[]>(path(report.id)+'/versions?before='+versions.at(-1)!.revision);if(reportRef.current?.id===report.id)setVersions(old=>[...old,...next]);}catch{setError('이전 이력을 불러오지 못했습니다.');}}
 const selected=report?.document.sections.find(s=>s.id===sectionId),historical=!!report&&report.revision!==report.current_revision;
 return {report,list,versions,sectionId,selected,busy,error,open,setOpen,mobilePane,setMobilePane,mode,setMode,research,setResearch,historical,accept,load,create,clear,select,changeSection,restore,moreVersions};
}
export type ReportsController=ReturnType<typeof useReports>;
