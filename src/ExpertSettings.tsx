import { useState } from 'react';
import { ArrowRight, Check, Plus, Sparkles, Users, X } from 'lucide-react';
import { completeFacets, facets, inferFacets, restrictedFacets, knowledgeDate, knowledgeNotes, ownedPapers, previewMembership } from './knowledge';
import type { ExpertCategory, ExpertFacets, Facet, Paper } from './types';
import './ExpertKnowledge.css';

export function ExpertSettings({categories,papers,onSave,onKnowledge}:{categories:ExpertCategory[];papers:Paper[];onSave:(value:ExpertCategory[])=>void;onKnowledge:(id:string)=>void}){
  const [activeId,setActiveId]=useState(categories[0]?.id||'');
  const [draft,setDraft]=useState<ExpertCategory>(()=>({...structuredClone(categories[0]),facets:completeFacets(categories[0]?.facets)}));
  const [stage,setStage]=useState<'define'|'preview'>('define');
  const [dirty,setDirty]=useState(false);
  const [notice,setNotice]=useState('');
  const isNew=!categories.some(item=>item.id===activeId);
  const ready=!!draft?.name.trim()&&!!draft?.scope.trim();
  const update=(value:Partial<ExpertCategory>)=>{setDraft(prev=>({...prev,...value}));setStage('define');setNotice('');};
  const select=(item:ExpertCategory)=>{setActiveId(item.id);setDraft({...structuredClone(item),facets:completeFacets(item.facets)});setStage('define');setDirty(false);setNotice('');};
  const add=()=>{const id=crypto.randomUUID();setActiveId(id);setDraft({id,name:'',scope:'',keywords:'',paperIds:[],reviewIds:[],excludedIds:[],facets:completeFacets(),enabled:true});setStage('define');setDirty(true);setNotice('');};
  const updateFacets=(value:ExpertFacets)=>{update({facets:value});setDirty(true);};
  const toggle=(key:Facet,value:string)=>{const current=draft.facets?.[key]||[];updateFacets({...draft.facets,[key]:current.includes(value)?current.filter(item=>item!==value):[...current,value]});};
  const preview=()=>{
    const criteria=draft.facets||{};
    const membership=dirty||isNew?previewMembership(criteria,papers,draft):{paperIds:draft.paperIds,reviewIds:draft.reviewIds||[]};
    setDraft(prev=>({...prev,facets:criteria,...membership,keywords:prev.keywords.trim()||[prev.name,...restrictedFacets(criteria).flatMap(facet=>criteria[facet.key]||[])].join(',')}));setStage('preview');setDirty(false);
  };
  const decide=(id:string,include:boolean)=>{setNotice('');setDraft(prev=>({...prev,reviewIds:(prev.reviewIds||[]).filter(item=>item!==id),paperIds:include?[...new Set([...prev.paperIds,id])]:prev.paperIds.filter(item=>item!==id),includedIds:include?[...new Set([...(prev.includedIds||[]),id])]:(prev.includedIds||[]).filter(item=>item!==id),excludedIds:include?(prev.excludedIds||[]).filter(item=>item!==id):[...new Set([...(prev.excludedIds||[]),id])]}));};
  const save=()=>{
    const value={...draft,name:draft.name.trim(),scope:draft.scope.trim(),updatedAt:knowledgeDate};
    onSave(isNew?[...categories,value]:categories.map(item=>item.id===activeId?value:item));
    setDraft(value);setNotice('전문가 지식을 구성했습니다. 지식 현황과 질문 흐름을 확인해 보세요.');
  };
  if(!draft)return null;
  const admitted=ownedPapers(draft,papers);
  const reviews=papers.filter(paper=>paper.status!=='pending'&&draft.reviewIds?.includes(paper.id));
  const excluded=papers.filter(paper=>draft.excludedIds?.includes(paper.id));
  const facetGroup=(keys:Facet[])=><>{facets.filter(facet=>keys.includes(facet.key)).map(facet=>{
    const selected=draft.facets?.[facet.key]??facet.values;
    const all=facet.values.every(value=>selected.includes(value));
    return <fieldset className="facet-field" key={facet.key}><legend>{facet.label}</legend>
      <div className="facet-selection-bar"><span>{all?'전체 선택 · 제한 없음':selected.length?`선택 ${selected.length} / ${facet.values.length}`:'선택 없음 · 자동 편입 없음'}</span><div><button type="button" className="quiet-button" disabled={all} onClick={()=>updateFacets({...draft.facets,[facet.key]:[...facet.values]})} aria-label={facet.label+' 전체 선택'}>전체 선택</button><button type="button" className="quiet-button" disabled={!selected.length} onClick={()=>updateFacets({...draft.facets,[facet.key]:[]})} aria-label={facet.label+' 전체 해제'}>전체 해제</button></div></div>
      <div className="facet-options">{facet.values.map(value=><label key={value} className={selected.includes(value)?'selected':''}><input type="checkbox" checked={selected.includes(value)} onChange={()=>toggle(facet.key,value)}/>{value}</label>)}</div>
    </fieldset>;
  })}</>;
  return <section className="expert-settings expert-studio-settings">
    <div className="block-heading"><div><h2>팀의 전문가 <span className="muted">{categories.length}</span></h2><p>전문가를 소개하고, 함께 살펴볼 논문을 정하세요.</p></div><button className="primary" onClick={add}><Plus size={16}/>전문가 만들기</button></div>
    <div className="expert-editor"><nav aria-label="전문가 설정 목록">{categories.map(item=><button type="button" className={item.id===activeId?'active':''} onClick={()=>select(item)} key={item.id}><Users size={16}/><span>{item.name}<small>{item.enabled?'사용 중':'사용 안 함'} · 담당 {ownedPapers(item,papers).length}편</small>{!!item.reviewIds?.length&&<small className="review-count">검토 대기 {item.reviewIds.length}편</small>}</span></button>)}{isNew&&<div className="new-expert-label"><Plus size={15}/>새 전문가 작성 중</div>}</nav>
      <form className="expert-setup-form" onSubmit={event=>{event.preventDefault();if(ready){if(stage==='define')preview();else save();}}}>
        <div className="setup-heading"><span className="eyebrow">{isNew?'NEW EXPERT':'EXPERT PROFILE'}</span><h2>{isNew?'어떤 전문가가 필요한가요?':draft.name}</h2><div className="setup-steps"><span className={stage==='define'?'current':''}>01 개요와 분류</span><ArrowRight size={13}/><span className={stage==='preview'?'current':''}>02 지식 확인</span></div></div>
        {stage==='define'?<div className="expert-fields">
          <label>전문가 이름<input required maxLength={40} placeholder="예: DRAM Reliability" value={draft.name} onChange={event=>update({name:event.target.value})}/></label>
          <label>전문가 개요<textarea aria-describedby="expert-overview-help" required rows={3} maxLength={500} placeholder="예: DRAM의 retention과 refresh를 연구합니다. 측정 조건을 바탕으로 논문의 주장과 reliability 이슈를 함께 살펴봅니다." value={draft.scope} onChange={event=>update({scope:event.target.value})}/></label>
          <p id="expert-overview-help" className="expert-field-help">전문성, 연구 관점, 도움을 줄 수 있는 질문을 소개하세요. 팀원이 전문가를 고를 때 표시됩니다.</p>
          <div className="classification-heading"><div><h3>논문 분류 조합</h3><p>전체 선택에서 필요 없는 항목을 해제하세요. 같은 항목 안은 OR, 항목 사이는 AND입니다.</p></div><button type="button" className="quiet-button" disabled={!ready} onClick={()=>updateFacets(completeFacets(inferFacets(draft.name+' '+draft.scope)))}><Sparkles size={14}/>개요로 분류 추천</button></div>
          <p className="classification-example">예: (DRAM 또는 HBM) + Reliability → 두 조건을 모두 만족하는 논문<br/>개요만 수정하면 분류는 유지됩니다. 추천을 누르면 아래 선택값에 반영됩니다.</p>
          {facetGroup(['platform','focus'])}
          <details className="expert-advanced"><summary>세부 분류와 질문 키워드 <span className="advanced-selection-summary">{restrictedFacets(draft.facets).some(facet=>['layer','application','method'].includes(facet.key))?'선택 조건 적용 중':'전체 선택'}</span></summary>{facetGroup(['layer','application','method'])}<label>질문 키워드<input maxLength={250} value={draft.keywords} onChange={event=>update({keywords:event.target.value})} placeholder="비워 두면 이름과 분류에서 구성합니다"/></label></details>
          {facets.some(facet=>draft.facets?.[facet.key]?.length===0)&&<p className="selection-empty-note" role="status">선택이 없는 항목이 있어 자동 편입되는 논문은 없습니다. 직접 편입한 논문은 유지됩니다.</p>}
          <div className="auto-admission-note"><Check size={16}/><div><strong>명확한 논문은 자동 편입</strong><p>담당 여부가 애매한 논문만 관리자 검토에 남깁니다. 새 전문가는 기존 논문 요약과 근거를 함께 활용합니다.</p></div></div>
          <label className="expert-enabled"><input type="checkbox" checked={draft.enabled} onChange={event=>update({enabled:event.target.checked})}/>팀원이 이 전문가에게 질문할 수 있도록 사용</label>
        </div>:<div className="knowledge-preview">
          <div className="knowledge-metrics"><div><strong>{admitted.length}</strong><span>담당 논문</span></div><div><strong>{knowledgeNotes(draft,papers).reduce((count,note)=>count+note.claims.length,0)}</strong><span>연결된 주장</span></div><div><strong>{reviews.length}</strong><span>검토 대기</span></div></div>
          <p className="preview-explanation">기존 논문 카드로 기본 지식을 구성합니다. 아래 결과는 가상 논문의 분류를 활용한 화면 예시입니다.</p>
          {!admitted.length&&<div className="knowledge-empty"><strong>아직 담당 논문이 없습니다.</strong><p>검토 대기 논문을 확인하거나 담당 분류를 조정하세요. 자료가 없는 전문가도 저장할 수 있으며 근거 답변은 보류됩니다.</p></div>}
          <h3>담당 지식에 포함될 논문</h3><div className="membership-list">{admitted.map(paper=><div key={paper.id}><span><strong>{paper.title}</strong><small>{paper.topic} · {paper.year}</small></span><button type="button" className="quiet-button" onClick={()=>decide(paper.id,false)} aria-label={paper.title+' 제외'}><X size={14}/>제외</button></div>)}</div>
          <div className="review-heading"><h3>판단이 필요한 논문</h3><span className="tag amber">{reviews.length}편</span></div>{reviews.length?<div className="membership-list review-list">{reviews.map(paper=><div key={paper.id}><span><strong>{paper.title}</strong><small>일부 분류가 미확정입니다. 이 전문가의 지식에 포함할지 확인하세요.</small></span><div><button type="button" className="secondary" onClick={()=>decide(paper.id,true)}><Check size={13}/>편입</button><button type="button" className="quiet-button" onClick={()=>decide(paper.id,false)}>제외</button></div></div>)}</div>:<p className="muted small-copy">검토할 논문이 없습니다.</p>}
          {!!excluded.length&&<details className="expert-advanced"><summary>제외한 논문 {excluded.length}편</summary><div className="membership-list">{excluded.map(paper=><div key={paper.id}><span>{paper.title}</span><button type="button" className="quiet-button" onClick={()=>decide(paper.id,true)}>편입으로 변경</button></div>)}</div></details>}
          <div className="auto-admission-note"><Sparkles size={17}/><div><strong>{admitted.length?'기본 지식부터 준비합니다':'관련 자료가 들어오면 지식을 쌓습니다'}</strong><p>확인된 근거로 주요 지식부터 정리하고, 주목할 논문과 아직 확인되지 않은 내용을 함께 보여줍니다.</p></div></div>
        </div>}
        {notice&&<div className="expert-saved" role="status"><Check size={18}/><div><strong>{draft.name}</strong><p>{notice}</p></div><button type="button" className="secondary" onClick={()=>onKnowledge(draft.id)}>지식 현황 보기<ArrowRight size={14}/></button></div>}
        <div className="settings-footer"><span>화면 예시 · 이 브라우저에 저장</span><div>{stage==='preview'&&<button className="secondary" type="button" onClick={()=>{setStage('define');setNotice('');}}>개요·분류 수정</button>}<button className="primary" type="submit" disabled={!ready}>{stage==='define'?<><Sparkles size={15}/>지식 구성 미리보기</>:<><Check size={15}/>{isNew?'전문가 생성':'변경사항 저장'}</>}</button></div></div>
      </form>
    </div>
  </section>;
}
