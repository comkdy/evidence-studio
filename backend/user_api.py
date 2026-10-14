"""Administrator-only user roles and access state. Identity is never editable here."""
import json
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from .pilot_db import connect, now
from .auth import require_permission, public_user, employee_id

router=APIRouter(prefix='/api/users',tags=['Users'])
Role=Literal['admin','superuser','user']


class UserCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    employee_id:str=Field(min_length=1,max_length=32,pattern=r'^[0-9]+$')
    role:Role='user'


class UserUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    role:Role
    active:bool
    revision:int=Field(ge=1)


def check_actor(db,actor):
    row=db.execute('SELECT role,active FROM users WHERE employee_id=?',(actor['employee_id'],)).fetchone()
    if not row or row['role']!='admin' or not row['active']:
        raise HTTPException(403,'관리자 권한이 변경되었습니다. 로그인 상태를 다시 확인해 주세요.')


def audit(db,actor,target,action,before,after):
    db.execute('INSERT INTO user_audit(actor_id,employee_id,action,before_json,after_json,created_at) VALUES(?,?,?,?,?,?)',
        (actor['employee_id'],target,action,json.dumps(before),json.dumps(after),now()))


@router.get('')
def list_users(q:str=Query('',max_length=100),limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0)):
    require_permission('manage_users')
    with connect() as db:
        term=q.strip().lower()
        where='WHERE instr(lower(employee_id || char(10) || name || char(10) || team),?)>0'
        total=db.execute('SELECT count(*) FROM users '+where,(term,)).fetchone()[0]
        rows=db.execute('SELECT * FROM users '+where+" ORDER BY active DESC,CASE role WHEN 'admin' THEN 0 WHEN 'superuser' THEN 1 ELSE 2 END,employee_id LIMIT ? OFFSET ?",(term,limit,offset)).fetchall()
        counts={r['role']:r['count'] for r in db.execute('SELECT role,count(*) AS count FROM users WHERE active=1 GROUP BY role')}
    return {'users':[public_user(r) for r in rows],'total':total,'active_counts':counts}


@router.post('',status_code=201)
def create_user(draft:UserCreate):
    actor=require_permission('manage_users')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');check_actor(db,actor)
        if db.execute('SELECT 1 FROM users WHERE employee_id=?',(draft.employee_id,)).fetchone():
            raise HTTPException(409,'이미 등록된 사번입니다.')
        stamp=now()
        db.execute('INSERT INTO users(employee_id,role,created_at,updated_at) VALUES(?,?,?,?)',(draft.employee_id,draft.role,stamp,stamp))
        audit(db,actor,draft.employee_id,'created',{}, {'role':draft.role,'active':True})
        row=db.execute('SELECT * FROM users WHERE employee_id=?',(draft.employee_id,)).fetchone()
        db.commit()
    return public_user(row)


@router.patch('/{target}')
def update_user(target:str,draft:UserUpdate):
    actor=require_permission('manage_users');employee_id(target)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');check_actor(db,actor)
        row=db.execute('SELECT * FROM users WHERE employee_id=?',(target,)).fetchone()
        if not row:raise HTTPException(404,'등록된 사용자가 아닙니다.')
        if row['revision']!=draft.revision:raise HTTPException(409,'다른 관리자가 변경했습니다. 목록을 새로고침하고 다시 시도해 주세요.')
        if target==actor['employee_id'] and (draft.role!=row['role'] or not draft.active):
            raise HTTPException(409,'본인의 권한이나 사용 상태는 다른 관리자가 변경해야 합니다.')
        if row['role']=='admin' and row['active'] and (draft.role!='admin' or not draft.active):
            if db.execute("SELECT count(*) FROM users WHERE active=1 AND role='admin'").fetchone()[0]<=1:
                raise HTTPException(409,'최소 한 명의 활성 관리자가 필요합니다.')
        old={'role':row['role'],'active':bool(row['active'])};new={'role':draft.role,'active':draft.active}
        if old!=new:
            db.execute('UPDATE users SET role=?,active=?,updated_at=?,revision=revision+1 WHERE employee_id=?',
                (draft.role,int(draft.active),now(),target))
            audit(db,actor,target,'access_changed',old,new)
        result=db.execute('SELECT * FROM users WHERE employee_id=?',(target,)).fetchone();db.commit()
    return public_user(result)
