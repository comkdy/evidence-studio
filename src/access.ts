export type UserRole = 'admin' | 'superuser' | 'user';
export const roleLabels: Record<UserRole, string> = { admin:'관리자', superuser:'슈퍼 유저', user:'일반 유저' };

// Build-time fixture for reviewing the mockup, not an authenticated session.
export function resolveDemoRole(value:string|undefined):UserRole {
  if(value===undefined)return 'admin';
  return value==='admin'||value==='superuser'?value:'user';
}
export function canManageExperts(role:unknown):boolean { return role==='admin'||role==='superuser'; }
export const canManageKnowledge=canManageExperts;
export function canManageUsers(role:unknown):boolean { return role==='admin'; }
