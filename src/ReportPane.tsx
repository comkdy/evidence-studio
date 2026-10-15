import { useState } from 'react';
import { BookOpen, Check, Download, FileText, History, Plus, X } from 'lucide-react';
import type { ReportsController } from './useReports';
import type { ReportSection, ReportStatement, ReportSource } from './reportData';
import { layoutLabels, statementLabels } from './reportData';
import { scientificNotation } from './scientificNotation';
import './ReportWorkspace.css';

export function ReportPane({state,locked,onPaper}:{state:ReportsController;locked:boolean;onPaper:(id:string)=>void}){
 const [title,setTitle]=useState('연구 보고서'),[purpose,setPurpose]=useState(''),[creating,setCreating]=useState(false);
 const r=state.report,disabled=locked||state.busy;
 return <aside className="report-pane" aria-label="보고서 편집">
  <header className="report-toolbar"><div><FileText size={19}/><strong>내 보고서</strong><span>본인만 열람</span></div><button aria-label="보고서 패널 닫기" disabled={disabled} onClick={()=>state.setOpen(false)}><X size={17}/></button></header>
  <div className="report-library"><select aria-label="저장한 보고서" disabled={disabled} value={r?.id??''} onChange={e=>{setCreating(false);void state.load(e.target.value);}}><option value="">보고서 선택</option>{state.list.map(item=><option key={item.id} value={item.id}>{item.title}</option>)}</select><button disabled={disabled} onClick={()=>setCreating(v=>!v)}><Plus size={15}/>새 보고서</button></div>
  {state.error&&<div className="report-error" role="alert">{state.error}<button disabled={disabled} onClick={()=>{if(r)void state.load(r.id);}}>최신 버전 불러오기</button></div>}
  {(creating||!r)&&<form className="report-create" onSubmit={e=>{e.preventDefault();void state.create(title,purpose).then(()=>setCreating(false));}}><div className="report-eyebrow">대화에서 보고서로</div><h2>근거를 하나의 보고서로 정리하세요.</h2><p>구간을 선택하고 왼쪽에서 질문하거나 수정을 요청하세요.</p><label>보고서 제목<input required maxLength={160} value={title} onChange={e=>setTitle(e.target.value)}/></label><label>목적 · 독자 <span>(선택)</span><textarea rows={2} maxLength={1000} value={purpose} onChange={e=>setPurpose(e.target.value)} placeholder="예: 팀 검토를 위한 DRAM retention 개선 방식 비교"/></label><button className="primary" disabled={disabled||!title.trim()} type="submit">보고서 만들기</button></form>}
  {r&&<>
   <div className="report-version-tools"><span><Check size={13}/>서버 저장 · v{r.revision}<button disabled={disabled} onClick={()=>void state.load(r.id)}>새로고침</button></span><a className={disabled?'disabled':''} aria-disabled={disabled} onClick={e=>{if(disabled)e.preventDefault();}} href={'/api/pilot/reports/'+encodeURIComponent(r.id)+'/export?revision='+r.revision} download><Download size={14}/>HTML 다운로드</a></div>
   <details className="report-history"><summary><History size={14}/>변경 이력 · 이전 버전 복원</summary><div>{state.versions.map(v=><button key={v.revision} disabled={disabled} aria-current={v.revision===r.revision?'true':undefined} onClick={()=>void state.load(r.id,v.revision)}><strong>v{v.revision}</strong><span>{v.instruction}<small>{new Date(v.created_at).toLocaleString('ko-KR')}{v.model?' · '+v.model:''}</small></span></button>)}{!!state.versions.length&&state.versions.at(-1)!.revision>1&&<button onClick={()=>void state.moreVersions()}>더 이전 이력</button>}</div></details>
   {state.historical&&<div className="report-version-preview"><p>v{r.revision} 미리보기 · 현재 v{r.current_revision}</p><button disabled={disabled} onClick={()=>void state.restore()}>이 버전으로 복원</button><button disabled={disabled} onClick={()=>void state.load(r.id)}>현재 버전 보기</button></div>}
   <div className="report-document"><header><div className="report-eyebrow">RESEARCH REPORT</div><h2>{r.document.title}</h2>{r.document.purpose&&<p>{r.document.purpose}</p>}<small>구간을 선택하면 해당 내용만 대상으로 대화합니다.</small></header>
    {r.document.sections.map((section,index)=><article className={'report-section '+(state.sectionId===section.id?'selected':'')} key={section.id}>
     <button className="report-section-select" disabled={disabled||state.historical} aria-pressed={state.sectionId===section.id} onClick={()=>state.select(section.id)} aria-label={section.title+' 구간 선택'}><span>{String(index+1).padStart(2,'0')}</span><h3>{section.title}</h3><small>{state.sectionId===section.id?'선택됨':'이 구간 선택'}</small></button>
     <SectionBody section={section}/>
     {!section.statements.length&&<p className="report-section-empty">선택한 뒤 ‘구간 수정’으로 초안을 요청하세요.</p>}
     {!!Object.keys(section.sources).length&&<details className="report-sources"><summary><BookOpen size={14}/>출처 확인 · {Object.keys(section.sources).length}개</summary>{Object.entries(section.sources).map(([key,source],i)=><Source key={key} number={i+1} source={source} onPaper={onPaper}/>)}</details>}
     {!state.historical&&<SectionSettings key={section.id+':'+r.revision} section={section} disabled={disabled} onSave={(title,layout)=>void state.changeSection('update',section.id,title,layout)} onDelete={()=>void state.changeSection('delete',section.id,section.title,section.layout)}/>}
    </article>)}
    {!state.historical&&<button className="report-add" disabled={disabled} onClick={()=>void state.changeSection('add','','새 구간','prose')}><Plus size={15}/>구간 추가</button>}
   </div>
  </>}
 </aside>;
}

function SectionBody({section}:{section:ReportSection}){
 const keys=Object.keys(section.sources);
 function text(st:ReportStatement){return <>{scientificNotation(st.text)}<span className="report-citations">{st.evidence_ids.map(key=><span key={key} title={section.sources[key]?.paper_title}>[{keys.indexOf(key)+1}]</span>)}</span></>;}
 if(section.layout==='table'&&section.statements.length)return <div className="report-table-scroll"><table><thead><tr><th>구분</th><th>비교 내용 · 조건 · 근거</th></tr></thead><tbody>{section.statements.map((st,i)=><tr key={i}><td>{statementLabels[st.kind]}</td><td>{text(st)}</td></tr>)}</tbody></table></div>;
 return <ul className={'report-statements '+section.layout}>{section.statements.map((st,i)=><li key={i}><small>{statementLabels[st.kind]}</small><p>{text(st)}</p></li>)}</ul>;
}
function Source({source,number,onPaper}:{source:ReportSource;number:number;onPaper:(id:string)=>void}){
 const spans=source.kind==='claim'?source.claim?.sources??[]:[{quote:source.text??'',page_number:source.page_number??0,document_id:source.document_id}];
 const paper=source.paper_id??source.claim?.paper_id;
 return <div className="report-source"><strong>[{number}] {source.paper_title}</strong>{source.claim&&<p>{scientificNotation(source.claim.summary)}</p>}{spans.map((s,i)=><div key={i}><blockquote>{s.quote}</blockquote>{s.document_id?<a target="_blank" rel="noreferrer" href={'/api/pilot/documents/'+encodeURIComponent(s.document_id)+'/pdf#page='+s.page_number}>p. {s.page_number} · 원문 PDF</a>:<small>p. {s.page_number}</small>}</div>)}{paper&&<button onClick={()=>onPaper(paper)}>논문 상세 보기</button>}<small>작성 당시 근거 사본</small></div>;
}
function SectionSettings({section,disabled,onSave,onDelete}:{section:ReportSection;disabled:boolean;onSave:(title:string,layout:ReportSection['layout'])=>void;onDelete:()=>void}){
 const [title,setTitle]=useState(section.title),[layout,setLayout]=useState(section.layout);
 return <details className="report-section-settings"><summary>제목 · 표시 형식</summary><form onSubmit={e=>{e.preventDefault();onSave(title,layout);}}><input aria-label={section.title+' 제목'} maxLength={160} required value={title} onChange={e=>setTitle(e.target.value)}/><select aria-label={section.title+' 표시 형식'} value={layout} onChange={e=>setLayout(e.target.value as ReportSection['layout'])}>{Object.entries(layoutLabels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select><button disabled={disabled||!title.trim()} type="submit">적용</button><button disabled={disabled} type="button" onClick={onDelete}>구간 삭제</button></form><small>내용은 유지됩니다. 삭제한 구간은 이전 버전에서 복원할 수 있습니다.</small></details>;
}
