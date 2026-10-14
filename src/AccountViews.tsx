import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Check, Lock, Plus, RefreshCw, Search, ShieldCheck, UserRound, X } from 'lucide-react';
import { roleLabels } from './access';
import type { UserRole } from './access';
import { api, displayName } from './identity';
import type { Account, Session } from './identity';
import { Pager } from './PilotViews';
import './Accounts.css';

const roles:UserRole[]=['admin','superuser','user'];
const capabilities={admin:'전문가 · 지식 · 유저 관리',superuser:'전문가 · 지식 관리',user:'질의 · 논문 · 지식 열람'};

export function LoginGate({error,retry}:{error:string;retry:()=>void}){
 return <main className="account-gate"><div><ShieldCheck size={30}/><p className="eyebrow">EVIDENCE STUDIO</p><h1>{error?'접속을 확인해 주세요':'로그인 상태를 확인하고 있습니다'}</h1><p>{error||'사용자 정보와 권한을 불러오고 있습니다.'}</p>{error&&<button className="primary" onClick={retry}>로그인 상태 다시 확인<ArrowRight size={16}/></button>}</div></main>;
}

export function ProfileCard({session,questions,bookmarks}:{session:Session;questions:number;bookmarks:number}){
 const user=session.user;
 return <section className="account-profile" aria-label="내 프로필"><header><span className="account-avatar"><UserRound size={27}/></span><div><h2>{displayName(session)}</h2><p>{user.team||'팀 정보 연결 대기'}</p></div><span className={'account-role '+user.role}>{roleLabels[user.role]}</span></header>
  <dl><div><dt>사번</dt><dd>{user.employee_id}</dd></div><div><dt>이름</dt><dd>{user.name||'SSO 연결 후 표시'}</dd></div><div><dt>로그인 방식</dt><dd>{session.mode==='local_admin'?'임시 관리자 접속':'사내 SSO'}</dd></div><div><dt>사용 권한</dt><dd>{capabilities[user.role]}</dd></div></dl>
  <footer><span>내 질문 <strong>{questions}</strong></span><span>저장한 논문 <strong>{bookmarks}</strong></span><small>{session.mode==='local_admin'?`현재는 사번 ${user.employee_id}으로 자동 접속합니다. 이름과 팀은 사내 SSO 연결 후 불러옵니다.`:'이름과 팀은 사내 사용자 정보에 따라 표시됩니다.'}</small></footer>
 </section>;
}

type UserList={users:Account[];total:number;active_counts:Partial<Record<UserRole,number>>};
export function UserManagement({session}:{session:Session}){
 const [data,setData]=useState<UserList>({users:[],total:0,active_counts:{}}),[q,setQ]=useState(''),[search,setSearch]=useState(''),[page,setPage]=useState(0),[reload,setReload]=useState(0);
 const [editing,setEditing]=useState<Account|'new'|null>(null),[busy,setBusy]=useState(true),[error,setError]=useState(''),[message,setMessage]=useState('');
 useEffect(()=>{const ctrl=new AbortController();setBusy(true);setError('');api<UserList>('/api/users?'+new URLSearchParams({q:search,offset:String(page*20),limit:'20'}),{signal:ctrl.signal}).then(setData).catch(e=>{if(e.name!=='AbortError')setError(e.message);}).finally(()=>{if(!ctrl.signal.aborted)setBusy(false);});return()=>ctrl.abort();},[page,search,reload]);
 return <section className="account-management">
  <div className="account-role-grid">{roles.map(role=><div key={role}><span>{roleLabels[role]}</span><strong>{data.active_counts[role]??0}<small>명</small></strong><p>{capabilities[role]}</p></div>)}</div>
  <div className="account-tools"><form onSubmit={e=>{e.preventDefault();setSearch(q.trim());setPage(0);}}><Search size={17}/><input aria-label="유저 검색" placeholder="이름, 사번 또는 팀으로 검색" value={q} onChange={e=>setQ(e.target.value)}/><button type="submit">검색</button></form><button className="secondary" aria-label="유저 목록 새로고침" disabled={busy} onClick={()=>setReload(v=>v+1)}><RefreshCw size={15}/></button><button className="primary" onClick={()=>setEditing('new')}><Plus size={16}/>사전 권한 지정</button></div>
  <p className="account-help">처음 SSO로 접속한 사용자는 이름·팀과 함께 일반 유저로 자동 등록됩니다. 회원가입이나 관리자 등록은 필요하지 않습니다. 관리자는 권한과 이용 상태만 관리하면 됩니다.</p>
  {error&&<p className="pilot-error" role="alert">{error}</p>}{message&&<p className="pilot-notice" role="status">{message}</p>}
  <div className="account-table-wrap"><table className="account-table"><thead><tr><th>사용자</th><th>사번</th><th>팀</th><th>권한</th><th>상태</th><th>관리</th></tr></thead><tbody>{data.users.map(user=><tr key={user.employee_id}><td><strong>{user.name||(user.employee_id===session.user.employee_id?displayName(session):'프로필 연결 대기')}</strong>{user.employee_id===session.user.employee_id&&<small className="account-me">나</small>}</td><td>{user.employee_id}</td><td>{user.team||'—'}</td><td><span className={'account-role '+user.role}>{roleLabels[user.role]}</span></td><td><span className={'account-state '+(user.active?'active':'')}>{user.active?'사용 중':'사용 중지'}</span></td><td><button className="secondary" disabled={user.employee_id===session.user.employee_id} title={user.employee_id===session.user.employee_id?'본인 권한은 다른 관리자가 변경할 수 있습니다.':undefined} onClick={()=>setEditing(user)}>권한 변경</button></td></tr>)}</tbody></table></div>
  {busy&&<p className="pilot-muted" role="status">사용자 정보를 불러오고 있습니다…</p>}{!busy&&!data.total&&<p className="pilot-empty">조건에 맞는 사용자가 없습니다.</p>}
  <Pager page={page} total={data.total} size={20} onPage={setPage} label="유저"/>
  {editing&&<UserEditor user={editing} close={()=>setEditing(null)} saved={()=>{setMessage(editing==='new'?'첫 SSO 접속에 적용할 권한을 지정했습니다.':'사용자 권한과 사용 상태를 저장했습니다.');setEditing(null);setReload(v=>v+1);}}/>}
 </section>;
}

function UserEditor({user,close,saved}:{user:Account|'new';close:()=>void;saved:()=>void}){
 const dialog=useRef<HTMLDialogElement>(null),isNew=user==='new';
 const [employeeId,setEmployeeId]=useState(isNew?'':user.employee_id),[role,setRole]=useState<UserRole>(isNew?'user':user.role),[active,setActive]=useState(isNew||user.active),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{dialog.current?.showModal();},[]);
 async function submit(){setBusy(true);setError('');try{await api('/api/users'+(isNew?'':'/'+employeeId),{method:isNew?'POST':'PATCH',body:JSON.stringify(isNew?{employee_id:employeeId,role}:{role,active,revision:user.revision})});saved();}catch(e){setError(e instanceof Error?e.message:'저장하지 못했습니다.');}finally{setBusy(false);}}
 return <dialog className="account-dialog" ref={dialog} onCancel={e=>{if(busy)e.preventDefault();else close();}} onClick={e=>{if(e.target===dialog.current&&!busy)close();}}><header><div><small>USER ACCESS</small><h2>{isNew?'사전 권한 지정':'사용자 권한 변경'}</h2></div><button aria-label="유저 설정 닫기" disabled={busy} onClick={close}><X size={21}/></button></header><form onSubmit={e=>{e.preventDefault();submit();}}><label className="account-field">사번<input aria-label="등록할 사번" required inputMode="numeric" pattern="[0-9]{1,32}" maxLength={32} disabled={!isNew||busy} value={employeeId} onChange={e=>setEmployeeId(e.target.value)}/></label><fieldset><legend>권한</legend>{roles.map(value=><label className="account-role-choice" key={value}><input type="radio" name="user-role" value={value} checked={role===value} disabled={busy} onChange={()=>setRole(value)}/><span><strong>{roleLabels[value]}</strong><small>{capabilities[value]}</small></span>{role===value&&<Check size={16}/>}</label>)}</fieldset>{!isNew&&<label className="account-active"><input type="checkbox" checked={active} disabled={busy} onChange={e=>setActive(e.target.checked)}/>이 사용자의 앱 이용 허용</label>}<p className="account-help">{isNew?'첫 접속 전 권한을 정해 두는 선택 기능입니다. 미리 지정하지 않아도 SSO 접속 시 일반 유저로 자동 등록됩니다.':'이름과 팀은 사내 사용자 정보에서 관리합니다. 사용을 중지해도 기존 기록은 보존됩니다.'}</p>{error&&<p className="pilot-error" role="alert">{error}</p>}<footer><button type="button" className="secondary" disabled={busy} onClick={close}>취소</button><button className="primary" type="submit" disabled={busy}>{busy?'저장 중…':'저장'}</button></footer></form></dialog>;
}

export function AccessDenied(){return <div className="pilot-empty"><Lock size={25}/><h2>이 메뉴에 접근할 권한이 없습니다.</h2><p>필요한 권한은 관리자에게 요청해 주세요.</p></div>;}
