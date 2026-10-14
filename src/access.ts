export type UserRole = 'admin' | 'superuser' | 'user';
export const roleLabels: Record<UserRole, string> = { admin:'관리자', superuser:'슈퍼 유저', user:'일반 유저' };
