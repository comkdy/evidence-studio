import { Fragment, useState } from 'react';
import type { ReactNode } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

// Reuse a small page window instead of rendering an ever-growing list.
export function KnowledgeList<T>({items,size=3,label,itemKey,children}:{items:T[];size?:number;label:string;itemKey:(item:T)=>string;children:(item:T)=>ReactNode}) {
  const [requestedPage,setPage]=useState(0);
  const page=Math.min(requestedPage,Math.max(0,Math.ceil(items.length/size)-1));
  const start=page*size;
  return <div className="bounded-list"><div className="bounded-items">{items.slice(start,start+size).map(item=><Fragment key={itemKey(item)}>{children(item)}</Fragment>)}</div>
    {items.length>size&&<nav className="bounded-pagination" aria-label={label+' 페이지'}><span aria-live="polite">{start+1}–{Math.min(start+size,items.length)} / {items.length}</span><button className="icon-button" aria-label={label+' 이전'} disabled={page===0} onClick={()=>setPage(page-1)}><ChevronLeft size={15}/></button><button className="icon-button" aria-label={label+' 다음'} disabled={start+size>=items.length} onClick={()=>setPage(page+1)}><ChevronRight size={15}/></button></nav>}
  </div>;
}
