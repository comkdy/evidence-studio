import type { UserRole } from './access';

export type Account={employee_id:string;name:string;team:string;role:UserRole;active:boolean;profile_source:string;profile_synced_at:string|null;created_at:string;updated_at:string;revision:number;permissions:{manage_experts:boolean;manage_knowledge:boolean;manage_users:boolean;refresh_wiki:boolean}};
export type Session={authenticated:true;mode:'local_admin'|'sso';user:Account};
export async function api<T>(path:string,init:RequestInit={}):Promise<T>{
 const response=await fetch(path,{...init,headers:{...(init.body?{'Content-Type':'application/json'}:{}),...init.headers}});
 const data=await response.json();
 if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'요청을 처리하지 못했습니다. 다시 시도해 주세요.');
 return data;
}
export function displayName(session:Session){return session.user.name||(session.mode==='local_admin'?'임시 관리자':'사용자');}
