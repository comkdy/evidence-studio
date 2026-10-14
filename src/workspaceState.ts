import type { Expert } from './pilotData';

// Browsing an inactive expert's papers remains possible; new questions require
// active experts. Saved answers keep their own names and evidence snapshots.
export function reconcileExpertScope(experts:Expert[],selectedIds:string[],filterId:string){
 const known=new Set(experts.map(expert=>expert.id));
 const active=new Set(experts.filter(expert=>expert.enabled!==0).map(expert=>expert.id));
 return {selectedIds:[...new Set(selectedIds)].filter(id=>active.has(id)),filterId:known.has(filterId)?filterId:''};
}

export function boundedPage(page:number,total:number,size:number){
 return Math.min(Math.max(0,page),Math.max(0,Math.ceil(total/size)-1));
}
