import { useState } from 'react';
import { SlidersHorizontal, X } from 'lucide-react';
import definitions from '../shared/expert_facets.json';
import './LibraryCategories.css';
export type CategorySelection=Record<string,string[]>;
export type CategoryCounts=Record<string,Record<string,number>>;
export function CategoryChips({value,onChange}:{value:CategorySelection;onChange:(v:CategorySelection)=>void}){
 return <div className="library-category-chips">{definitions.flatMap(f=>(value[f.key]||[]).map(tag=><button key={f.key+tag} onClick={()=>onChange({...value,[f.key]:value[f.key].filter(x=>x!==tag)})}>{tag==='__unclassified__'?f.label+' 미분류':tag}<X size={11}/></button>))}</div>;
}
export function LibraryCategories({value,counts,total,onChange}:{value:CategorySelection;counts:CategoryCounts;total:number;onChange:(v:CategorySelection)=>void}){
 const [open,setOpen]=useState(false),active=Object.values(value).reduce((n,v)=>n+v.length,0);
 function toggle(key:string,tag:string){const values=value[key]||[];onChange({...value,[key]:values.includes(tag)?values.filter(x=>x!==tag):[...values,tag]});}
 const section=(title:string,keys:string[])=> <section className="library-category-section"><h3>{title}</h3>{definitions.filter(f=>keys.includes(f.key)).map(f=><details key={f.key} open={f.key==='platform'||f.key==='focus'?true:undefined}><summary><span>{f.label}</span>{!!value[f.key]?.length&&<small>{value[f.key].length} 선택</small>}</summary><div>{[...f.values,'__unclassified__'].map(tag=><label key={tag} className={value[f.key]?.includes(tag)?'selected':''}><input type="checkbox" aria-label={f.label+' '+(tag==='__unclassified__'?'미분류':tag)} checked={value[f.key]?.includes(tag)||false} onChange={()=>toggle(f.key,tag)}/><span>{tag==='__unclassified__'?'미분류':tag}</span><small>{counts[f.key]?.[tag]??0}</small></label>)}</div></details>)}</section>;
 return <><button className="library-category-toggle secondary" onClick={()=>setOpen(true)}><SlidersHorizontal size={15}/>카테고리{active>0&&<span>{active}</span>}</button>{open&&<button className="library-category-shade" aria-label="카테고리 닫기" onClick={()=>setOpen(false)}/>}<aside className={'library-categories '+(open?'is-open':'')} aria-label="논문 카테고리"><header><div><span>RESEARCH FILTERS</span><h2>카테고리</h2></div><button disabled={!active} onClick={()=>onChange({})}>초기화</button><button className="library-category-close" aria-label="카테고리 패널 닫기" onClick={()=>setOpen(false)}><X size={17}/></button></header><div className="library-category-description"><strong>{active?total+'편의 논문':'전체 논문'}</strong><p>같은 항목 안은 OR, 항목 사이는 AND로 좁힙니다. 숫자는 다른 선택 조건을 반영한 논문 수입니다.</p></div>{section('연구 대상',['platform','application'])}{section('연구 관점',['focus','layer'])}{section('추가 분류',['method'])}<footer><button className="primary" onClick={()=>setOpen(false)}>논문 {total}편 보기</button></footer></aside></>;
}

