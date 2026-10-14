"""Request-scoped identity and server-side role checks; temporary admin by default."""
import os
import re
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from dotenv import dotenv_values
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from .pilot_db import ROOT, connect, now
from . import sso_adapter

from .identity_settings import bootstrap_admin_id
ROLE_LABELS = {'admin':'관리자', 'superuser':'슈퍼 유저', 'user':'일반 유저'}
PERMISSIONS = {
    'admin': frozenset(('manage_experts','manage_knowledge','manage_users','refresh_wiki')),
    'superuser': frozenset(('manage_experts','manage_knowledge')),
    'user': frozenset(),
}
principal_context = ContextVar('evidence_studio_principal', default=None)
router = APIRouter(prefix='/api/auth', tags=['Identity'])


def auth_settings():
    values={**dotenv_values(ROOT/'.env',encoding='utf-8-sig',interpolate=False),**os.environ}
    mode=(values.get('AUTH_MODE') or 'local_admin').strip()
    if mode not in ('local_admin','sso'):
        raise HTTPException(503,'로그인 방식 설정을 확인해 주세요.')
    return mode,(values.get('SSO_COOKIE_NAME') or '').strip()


def employee_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,32}',value):
        raise HTTPException(422,'사번은 숫자 1~32자리로 입력해 주세요.')
    return value


def public_user(row):
    user=dict(row)
    user['active']=bool(user['active'])
    user['role_label']=ROLE_LABELS[user['role']]
    user['permissions']={p:p in PERMISSIONS[user['role']] for p in PERMISSIONS['admin']}
    return user


def local_user():
    try:
        admin_id = bootstrap_admin_id()
    except ValueError as error:
        raise HTTPException(503, str(error)) from None
    with connect() as db:
        row=db.execute('SELECT * FROM users WHERE employee_id=?',(admin_id,)).fetchone()
    if not row or not row['active']:
        raise HTTPException(403,'초기 관리자 계정의 사용 상태를 확인해 주세요.')
    return public_user(row)


def current_user():
    user=principal_context.get()
    if user is not None:
        return user
    # Only CLI/direct local calls use this branch. HTTP always resolves identity.
    if auth_settings()[0]=='local_admin':
        return local_user()
    raise HTTPException(401,'사내 SSO 로그인이 필요합니다.')


def has_permission(permission):
    user=current_user()
    return user['active'] and permission in PERMISSIONS.get(user['role'],frozenset())


def require_permission(permission):
    if not has_permission(permission):
        raise HTTPException(403,'이 작업에 대한 권한이 없습니다.')
    return current_user()


def resolve_user(request):
    mode,cookie_name=auth_settings()
    if mode=='local_admin':
        return local_user()
    if not cookie_name:
        raise HTTPException(503,'사내 SSO 쿠키 이름을 설정해야 합니다.')
    cookie=request.cookies.get(cookie_name)
    if not cookie:
        raise HTTPException(401,'사내 SSO 로그인이 필요합니다. 사내 로그인 후 다시 확인해 주세요.')
    try:
        verified_id=employee_id(sso_adapter.verify_sso_cookie(cookie))
        with connect() as db:
            row=db.execute('SELECT * FROM users WHERE employee_id=?',(verified_id,)).fetchone()
        if row and not row['active']:
            raise HTTPException(403,'사용이 중지된 계정입니다. 관리자에게 문의해 주세요.')
        # Company directory data is cached; role and active state remain live.
        stale=not row or not row['profile_synced_at'] or datetime.now(timezone.utc)-datetime.fromisoformat(row['profile_synced_at'])>timedelta(hours=1)
        if stale:
            # First verified SSO visit provisions the account automatically.
            # Repeated/concurrent visits share the employee_id primary key;
            # directory synchronization never overwrites role or active state.
            profile=sso_adapter.fetch_employee_profile(verified_id)
            if profile.employee_id!=verified_id or not isinstance(profile.name,str) or not profile.name.strip() or len(profile.name)>120 or not isinstance(profile.team,str) or len(profile.team)>200:
                raise HTTPException(503,'사내 프로필 응답을 확인해야 합니다.')
            stamp=now()
            with connect() as db:
                db.execute('''INSERT INTO users(employee_id,name,team,role,profile_source,profile_synced_at,created_at,updated_at)
                  VALUES(?,?,?,'user','sso',?,?,?) ON CONFLICT(employee_id) DO UPDATE SET
                  name=excluded.name,team=excluded.team,profile_source='sso',profile_synced_at=excluded.profile_synced_at''',
                  (verified_id,profile.name.strip(),profile.team.strip(),stamp,stamp,stamp))
                db.commit()
        with connect() as db:
            row=db.execute('SELECT * FROM users WHERE employee_id=?',(verified_id,)).fetchone()
        if not row['active']:
            raise HTTPException(403,'사용이 중지된 계정입니다.')
        return public_user(row)
    except HTTPException:
        raise
    except Exception:
        # Never expose raw cookies, internal API responses, URLs or credentials.
        raise HTTPException(503,'사내 인증 또는 프로필 조회를 완료하지 못했습니다.') from None


def check_request_origin(request):
    if request.method in ('GET','HEAD','OPTIONS'):
        return
    origin=request.headers.get('origin')
    expected=f'{request.url.scheme}://{request.url.netloc}'
    if request.headers.get('sec-fetch-site')=='cross-site' or (origin and origin!=expected):
        raise HTTPException(403,'동일한 앱에서 요청을 다시 실행해 주세요.')
    if auth_settings()[0]=='sso' and not origin:
        raise HTTPException(403,'요청 출처를 확인할 수 없습니다.')


async def authentication_middleware(request, call_next):
    if not request.url.path.startswith('/api/') or request.url.path=='/api/health':
        return await call_next(request)
    try:
        check_request_origin(request)
        user=await run_in_threadpool(resolve_user,request)
    except HTTPException as error:
        return JSONResponse({'detail':error.detail},status_code=error.status_code,headers={'Cache-Control':'no-store'})
    token=principal_context.set(user)
    try:
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        return response
    finally:
        principal_context.reset(token)


@router.get('/session')
def session():
    return {'authenticated':True,'mode':auth_settings()[0],'user':current_user()}
