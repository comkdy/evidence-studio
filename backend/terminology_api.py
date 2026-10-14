"""Team glossary: read/propose for everyone, approve/edit for knowledge managers."""
from typing import Literal
import json
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from .auth import current_user, has_permission, require_permission
from .pilot_db import connect, now
from .terminology import audit, encoded, equivalent, public, pure_unit, query_rules

router=APIRouter(prefix='/api/pilot/terminology',tags=['Terminology'])


class TermInput(BaseModel):
    preferred: str=Field(min_length=1,max_length=120)
    aliases: list[str]=Field(default_factory=list,max_length=24)
    status: Literal['draft','approved','archived']='draft'
    revision: int=Field(default=0,ge=0)


class CreateTerm(TermInput):
    id: UUID


def cleaned(data):
    import re
    preferred=data.preferred.strip()
    if not re.search('[A-Za-z]',preferred) or re.search('[가-힣]',preferred):
        raise HTTPException(422,'대표 용어는 영어 원문 또는 약어로 입력하세요.')
    if pure_unit(preferred):
        raise HTTPException(422,'단위만 등록할 수 없습니다. 수치가 의미하는 parameter 또는 개념을 입력하세요.')
    def values(items):
        out=[];seen={preferred.casefold()}
        for value in items:
            value=value.strip()
            if not value:continue
            if len(value)>120 or any(ord(c)<32 for c in value):raise HTTPException(422,'각 용어는 줄바꿈 없이 120자 이내로 입력하세요.')
            if pure_unit(value):raise HTTPException(422,'동의어에 단위만 등록할 수 없습니다.')
            if value.casefold() not in seen:out.append(value);seen.add(value.casefold())
        return out
    if any(ord(c)<32 for c in preferred):raise HTTPException(422,'대표 용어는 한 줄로 입력하세요.')
    return dict(preferred=preferred,aliases=values(data.aliases),status=data.status)


def save(db,term_id,data,actor,old=None):
    value=cleaned(data)
    if old and all(old[k]==value[k] for k in value):return old
    if value['status']=='approved':
        keys={equivalent(v) for v in [value['preferred'],*value['aliases']]}
        for row in db.execute("SELECT * FROM terminology_entries WHERE status='approved' AND id<>?",(term_id,)):
            other=public(row)
            if keys.intersection(equivalent(v) for v in [other['preferred'],*other['aliases']]):
                raise HTTPException(409,'이미 승인된 '+other['preferred']+'의 대표 용어 또는 동의어와 겹칩니다. 의미를 구분하거나 기존 항목을 수정하세요.')
    # Legacy metadata is retained for history compatibility, never edited or used.
    legacy={key:old[key] if old else default for key,default in (('related',[]),('scope',''),('note',''))}
    stamp=now();item=dict(id=term_id,**value,**legacy,revision=(old['revision']+1 if old else 1),
        created_by=old['created_by'] if old else actor,updated_by=actor,
        created_at=old['created_at'] if old else stamp,updated_at=stamp)
    db.execute('INSERT INTO terminology_entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
        'preferred=excluded.preferred,aliases_json=excluded.aliases_json,related_json=excluded.related_json,scope=excluded.scope,note=excluded.note,status=excluded.status,revision=excluded.revision,updated_by=excluded.updated_by,updated_at=excluded.updated_at',
        (term_id,item['preferred'],encoded(item['aliases']),encoded(item['related']),item['scope'],item['note'],item['status'],item['revision'],item['created_by'],actor,item['created_at'],stamp))
    audit(db,item,actor)
    if value['status']=='approved' or old and old['status']=='approved':
        db.execute('UPDATE terminology_meta SET revision=revision+1 WHERE id=1')
    return item


@router.get('')
def list_terms(q:str=Query('',max_length=160),status:Literal['all','approved','draft','archived']='all',
               offset:int=Query(0,ge=0),limit:int=Query(12,ge=1,le=60)):
    with connect() as db:
        items=[public(r) for r in db.execute('SELECT * FROM terminology_entries ORDER BY preferred COLLATE NOCASE,id')]
        revision=db.execute('SELECT revision FROM terminology_meta WHERE id=1').fetchone()[0]
    counts={key:sum(t['status']==key for t in items) for key in ('approved','draft','archived')}
    filtered=[t for t in items if (status=='all' or t['status']==status) and (not q or equivalent(q) in equivalent(' '.join([t['preferred'],*t['aliases']])))]
    return dict(terms=filtered[offset:offset+limit],total=len(filtered),counts=counts,revision=revision)


class Preview(BaseModel):
    text:str=Field(min_length=1,max_length=1000)


@router.post('/preview')
def preview(data:Preview):return query_rules(data.text)


@router.post('',status_code=201)
def create(data:CreateTerm):
    actor=current_user()['employee_id']
    if not has_permission('manage_knowledge') and data.status!='draft':raise HTTPException(403,'일반 유저는 검토 대기 상태로 제안할 수 있습니다.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT * FROM terminology_entries WHERE id=?',(str(data.id),)).fetchone()
        if old:
            item=public(old);value=cleaned(data)
            if item['created_by']==actor and all(item[k]==value[k] for k in value):return item
            raise HTTPException(409,'동일한 등록 요청이 이미 처리되었습니다. 목록을 새로고침하세요.')
        item=save(db,str(data.id),data,actor);db.commit()
    return item


@router.get('/{term_id}')
def detail(term_id:str):
    with connect() as db:
        row=db.execute('SELECT * FROM terminology_entries WHERE id=?',(term_id,)).fetchone()
        if not row:raise HTTPException(404,'용어를 찾을 수 없습니다.')
        history=[dict(snapshot=json.loads(r['snapshot_json']),actor=r['actor'],actor_name=r['actor_name'],created_at=r['created_at']) for r in db.execute(
            "SELECT r.*,COALESCE(NULLIF(u.name,''),r.actor) AS actor_name FROM terminology_revisions r LEFT JOIN users u ON u.employee_id=r.actor WHERE term_id=? ORDER BY revision DESC LIMIT 30",(term_id,))]
    return dict(term=public(row),history=history)


@router.put('/{term_id}')
def update(term_id:str,data:TermInput):
    actor=require_permission('manage_knowledge')['employee_id']
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM terminology_entries WHERE id=?',(term_id,)).fetchone()
        if not row:raise HTTPException(404,'용어를 찾을 수 없습니다.')
        old=public(row)
        if data.revision!=old['revision']:raise HTTPException(409,'다른 사용자가 변경했습니다. 내용을 복사한 뒤 창을 다시 열어 최신 내용을 확인하세요.')
        item=save(db,term_id,data,actor,old);db.commit()
    return item
