import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, Lock, SlidersHorizontal, Sparkles, Users, X } from 'lucide-react';
import type { Answer, ExpertCategory, ExpertSelection } from './types';
import './QuestionOptions.css';

type Props = {
  visibility:Answer['visibility']; onVisibility:(value:Answer['visibility'])=>void;
  mode:ExpertSelection; onMode:(value:ExpertSelection)=>void;
  categories:ExpertCategory[]; selectedIds:string[]; onSelection:(ids:string[])=>void;
  disabled:boolean;
};

export function QuestionOptions({visibility,onVisibility,mode,onMode,categories,selectedIds,onSelection,disabled}:Props) {
  const [panel,setPanel]=useState<'visibility'|'experts'|null>(null);
  const root=useRef<HTMLDivElement>(null);
  const popup=useRef<HTMLDivElement>(null);
  const visibilityTrigger=useRef<HTMLButtonElement>(null);
  const expertTrigger=useRef<HTMLButtonElement>(null);
  const enabled=categories.filter(item=>item.enabled);
  const selected=enabled.filter(item=>selectedIds.includes(item.id));
  const expertLabel=mode==='auto'?'전문가 자동 선택':selected.length===1?selected[0].name:selected.length?`전문가 ${selected.length}명`:'전문가 직접 선택';
  const close=()=>{setPanel(null);(panel==='visibility'?visibilityTrigger:expertTrigger).current?.focus();};

  useEffect(()=>{if(disabled)setPanel(null);},[disabled]);
  useEffect(()=>{
    if(!panel)return;
    (popup.current?.querySelector<HTMLElement>('input:checked')||popup.current?.querySelector<HTMLElement>('button, input'))?.focus();
    const outside=(event:PointerEvent)=>{if(!root.current?.contains(event.target as Node))setPanel(null);};
    document.addEventListener('pointerdown',outside);
    return()=>document.removeEventListener('pointerdown',outside);
  },[panel]);

  return <div className="question-options" ref={root}
    onKeyDown={event=>{if(event.key==='Escape'&&panel){event.preventDefault();event.stopPropagation();close();}}}
    onBlur={event=>{if(event.relatedTarget&&!event.currentTarget.contains(event.relatedTarget))setPanel(null);}}>
    <div className="question-option-triggers">
      <button type="button" ref={visibilityTrigger} className={`option-trigger ${visibility==='private'?'is-private':''}`} disabled={disabled}
        aria-label={`공개 범위: ${visibility==='team'?'팀에 공유':'나만 보기'}`} aria-haspopup="dialog" aria-expanded={panel==='visibility'} aria-controls={panel==='visibility'?'visibility-options':undefined}
        onClick={()=>setPanel(panel==='visibility'?null:'visibility')}>
        {visibility==='team'?<Users size={15}/>:<Lock size={15}/>}<span>{visibility==='team'?'팀에 공유':'나만 보기'}</span><ChevronDown size={12}/>
      </button>
      <button type="button" ref={expertTrigger} className={`option-trigger expert-trigger ${mode==='manual'?'is-manual':''}`} disabled={disabled}
        aria-label={`전문가 선택: ${expertLabel}`} aria-haspopup="dialog" aria-expanded={panel==='experts'} aria-controls={panel==='experts'?'expert-options':undefined}
        onClick={()=>setPanel(panel==='experts'?null:'experts')}>
        {mode==='auto'?<Sparkles size={15}/>:<SlidersHorizontal size={15}/>}<span>{expertLabel}</span><ChevronDown size={12}/>
      </button>
    </div>
    {panel&&<div ref={popup} id={panel==='visibility'?'visibility-options':'expert-options'} className={`question-popover ${panel==='experts'?'expert-popover':''}`} role="dialog" aria-label={panel==='visibility'?'질문 공개 범위':'질문할 전문가'}>
      <div className="option-heading"><strong>{panel==='visibility'?'질문 공개 범위':'질문할 전문가'}</strong><button type="button" className="icon-button" aria-label="선택 메뉴 닫기" onClick={close}><X size={15}/></button></div>
      {panel==='visibility'?<div className="visibility-options">
        {([{value:'team',title:'팀에 공유',description:'팀원이 질문과 답변을 함께 볼 수 있어요.',Icon:Users},{value:'private',title:'나만 보기',description:'내 페이지에서 나만 확인할 수 있어요.',Icon:Lock}] as const).map(({value,title,description,Icon})=><label className={`option-card ${visibility===value?'is-selected':''}`} key={value}>
          <input className="sr-only" type="radio" name="question-visibility" value={value} checked={visibility===value} onChange={()=>{onVisibility(value);close();}}/>
          <span className="option-icon"><Icon size={18}/></span><span className="option-copy"><strong>{title}</strong><small>{description}</small></span>{visibility===value&&<Check size={17} className="option-check"/>}
        </label>)}
        <p className="option-hint">내 질문의 공개 범위는 나중에도 바꿀 수 있어요.</p>
      </div>:<>
        <div className="expert-mode-switch" role="group" aria-label="전문가 선택 방식">
          <label className={mode==='auto'?'active':''}><input type="radio" className="sr-only" name="expert-mode" checked={mode==='auto'} onChange={()=>onMode('auto')}/><Sparkles size={14}/>자동 선택</label>
          <label className={mode==='manual'?'active':''}><input type="radio" className="sr-only" name="expert-mode" checked={mode==='manual'} onChange={()=>onMode('manual')}/><SlidersHorizontal size={14}/>직접 선택</label>
        </div>
        {mode==='auto'?<div className="expert-auto-copy"><span className="auto-selection-icon"><Sparkles size={21}/></span><strong>질문에 맞는 전문가를 먼저 추천</strong><p>질문을 보내면 추천 이유와 담당 지식을 확인합니다.<br/>선택한 전문가는 자기 담당 논문에서 답합니다.</p></div>:<>
          <p className="option-hint expert-list-hint">함께 답변할 전문가를 선택하세요. 여러 명 선택할 수 있어요.</p>
          <div className="expert-choice-list">{enabled.map(expert=><label key={expert.id} className={`expert-choice ${selectedIds.includes(expert.id)?'is-selected':''}`}>
            <input type="checkbox" checked={selectedIds.includes(expert.id)} onChange={event=>onSelection(event.target.checked?[...selectedIds,expert.id]:selectedIds.filter(id=>id!==expert.id))}/>
            <span><strong>{expert.name}</strong><small>{expert.scope}</small></span>
          </label>)}</div>
        </>}
        {!enabled.length&&<p className="option-hint">사용 가능한 전문가가 없습니다. 관리자에게 문의해 주세요.</p>}
        <div className="expert-option-footer"><span>{mode==='auto'?'질문에 따라 자동으로 선택':`${selected.length}명 선택됨`}</span><button type="button" className="primary" disabled={mode==='manual'&&!selected.length} onClick={close}>선택 완료<Check size={14}/></button></div>
      </>}
    </div>}
  </div>;
}
