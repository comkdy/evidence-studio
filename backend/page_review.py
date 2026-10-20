"""Human correction of extracted text, with immutable history and exact citations."""
import hashlib
import json
import re
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .auth import current_user, has_permission, require_permission
from .pilot_db import connect, now
from .chunking import reindex_document, load_chunk_policy, prepare_document_index, validate_prepared_input, IndexInputsChanged

router=APIRouter(prefix='/api/pilot',tags=['Source review'])


def problem_details(page):
    text=page['text']
    bad=[i for i,c in enumerate(text) if c=='\ufffd' or ord(c)<32 and c not in '\n\r\t']
    snippets=[]
    for i in bad:
        snippet=text[max(0,i-24):i+40]
        snippet=''.join('�' if c=='\ufffd' or ord(c)<32 and c not in '\n\r\t' else c for c in snippet)
        if not snippets or snippet not in snippets:
            snippets.append(snippet)
        if len(snippets)==3:break
    compact=len(re.sub(r'\s','',text))
    return {'characters':compact,'suspicious_characters':len(bad),'snippets':snippets,
            'reason':f'추출된 텍스트가 {compact}자뿐입니다. 그림·표 또는 스캔 이미지의 내용을 읽지 못했을 수 있습니다.' if page['status']=='needs_ocr'
            else f'깨졌거나 읽기 어려운 문자 {len(bad)}개가 감지되었습니다. 본문·수치·단위를 원본과 대조해 주세요.' if bad
            else '이 페이지의 텍스트가 원본과 일치하는지 검토가 필요합니다.'}


def current_page(db,page_id):
    row=db.execute('''SELECT pg.*,d.paper_id,d.id AS document_id,p.title FROM pages pg
        JOIN paper_documents d ON d.current_run_id=pg.run_id JOIN papers p ON p.id=d.paper_id WHERE pg.id=?''',(page_id,)).fetchone()
    if not row:raise HTTPException(404,'현재 문서의 페이지를 찾을 수 없습니다.')
    return dict(row)


def revision(db,page):
    last=db.execute('SELECT request_id FROM page_review_changes WHERE page_id=? ORDER BY applied_at DESC LIMIT 1',(page['id'],)).fetchone()
    return hashlib.sha256(json.dumps([page,last[0] if last else None],sort_keys=True).encode()).hexdigest()


def review_records(db,page_id):
    return [dict(r) for r in db.execute('''SELECT r.id,r.action,r.proposed_text,r.reason,r.state,r.decision_note,r.created_at,r.decided_at,
        coalesce(nullif(u.name,''),'사용자') AS actor_name,
        coalesce(nullif(v.name,''),'관리자') AS reviewer_name FROM page_review_requests r
        JOIN users u ON u.employee_id=r.actor_id LEFT JOIN users v ON v.employee_id=r.reviewer_id
        WHERE r.page_id=? ORDER BY r.created_at DESC LIMIT 20''',(page_id,))]


@router.get('/page-reviews/{page_id}')
def review_detail(page_id:str):
    with connect() as db:
        page=current_page(db,page_id)
        original=db.execute('SELECT before_text FROM page_review_changes WHERE page_id=? ORDER BY applied_at LIMIT 1',(page_id,)).fetchone()
        history=review_records(db,page_id)
        return {'page':page,'revision':revision(db,page),'problem':problem_details(page),
                'original_text':original[0] if original else page['text'],
                'linked_evidence':db.execute('SELECT count(*) FROM evidence_spans WHERE page_id=?',(page_id,)).fetchone()[0],
                'can_apply':has_permission('manage_knowledge'),'history':history}


class ReviewInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:UUID
    revision:str=Field(min_length=64,max_length=64)
    action:Literal['correct','verify','exclude','reopen']
    text:str=Field(default='',max_length=150000)
    reason:str=Field(min_length=3,max_length=1000)
    confirmed:bool=False


class ReviewDecision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['apply','reject']
    revision:str=Field(min_length=64,max_length=64)
    note:str=Field(default='',max_length=1000)
    confirmed:bool=False


def validate_change(db,page,action,text):
    if action in ('correct','verify'):
        if action=='verify':text=page['text']
        if len(re.sub(r'\s','',text))<20 or sum(c.isalpha() for c in text)<10:
            raise HTTPException(422,'검색에 쓸 본문이나 caption을 보완해 주세요. 페이지 번호만으로는 완료 처리할 수 없습니다.')
        if any(c=='\ufffd' or ord(c)<32 and c not in '\n\r\t' for c in text):
            raise HTTPException(422,'깨진 문자나 제어 문자가 남아 있습니다. 원본과 대조하여 보완해 주세요.')
        # Preserve existing cited statements and numbers exactly; only relocate offsets.
        for span in db.execute('SELECT * FROM evidence_spans WHERE page_id=?',(page['id'],)):
            if text[span['start_offset']:span['end_offset']]==span['quote']:continue
            if text.count(span['quote'])!=1:
                raise HTTPException(409,'기존 claim·수치의 인용문이 달라지거나 위치가 모호합니다. 인용문을 유지한 텍스트만 저장할 수 있습니다.')
    elif db.execute('SELECT 1 FROM evidence_spans WHERE page_id=? LIMIT 1',(page['id'],)).fetchone():
        raise HTTPException(409,'기존 claim·수치가 인용한 페이지는 검색에서 제외할 수 없습니다. 연결된 근거를 먼저 검토해 주세요.')
    return text


def prepare_review_index(db,page,request):
    if revision(db,page)!=request['revision']:
        raise HTTPException(409,'다른 사용자가 이 페이지를 수정했습니다. 최신 내용을 다시 열어 주세요.')
    text=validate_change(db,page,request['action'],request['proposed_text'])
    state='excluded' if request['action']=='exclude' else 'needs_review' if request['action']=='reopen' else 'ready'
    if request['action'] in ('exclude','reopen'):text=page['text']
    try:
        return prepare_document_index(db,page['run_id'],load_chunk_policy(local_only=True),
                                      page_updates={page['id']:{'text':text,'status':state}})
    except Exception as error:
        raise HTTPException(503,'검색 준비에 실패해 변경사항을 저장하지 않았습니다. 검색 서비스 연결을 확인한 뒤 다시 시도해 주세요.') from error


def apply_change(db,page,request,reviewer_id,prepared):
    try:validate_prepared_input(db,prepared)
    except IndexInputsChanged as error:
        raise HTTPException(409,'검색 준비 중 문서가 변경되었습니다. 최신 내용을 다시 열어 검토해 주세요.') from error
    if revision(db,page)!=request['revision']:
        raise HTTPException(409,'다른 사용자가 이 페이지를 수정했습니다. 최신 내용을 다시 열어 검토해 주세요.')
    action=request['action'];text=validate_change(db,page,action,request['proposed_text'])
    state='excluded' if action=='exclude' else 'needs_review' if action=='reopen' else 'ready'
    if action in ('exclude','reopen'):text=page['text']
    method='human_corrected' if action=='correct' else 'human_verified' if action=='verify' else page['method']
    offsets=[]
    for span in db.execute('SELECT * FROM evidence_spans WHERE page_id=?',(page['id'],)).fetchall():
        start=span['start_offset'] if text[span['start_offset']:span['end_offset']]==span['quote'] else text.index(span['quote'])
        end=start+len(span['quote'])
        offsets.append({'id':span['id'],'before':[span['start_offset'],span['end_offset']],'after':[start,end]})
        db.execute('UPDATE evidence_spans SET start_offset=?,end_offset=? WHERE id=?',(start,end,span['id']))
    stamp=now()
    db.execute('''INSERT INTO page_review_changes VALUES(?,?,?,?,?,?,?,?,?,?)''',
        (request['id'],page['id'],page['text'],page['status'],page['method'],text,state,method,json.dumps(offsets),stamp))
    db.execute('UPDATE pages SET text=?,status=?,method=?,char_count=? WHERE id=?',(text,state,method,len(text),page['id']))
    pending=db.execute("SELECT count(*) FROM pages WHERE run_id=? AND status NOT IN ('ready','excluded')",(page['run_id'],)).fetchone()[0]
    usable=db.execute("SELECT count(*) FROM pages WHERE run_id=? AND status IN ('ready','excluded')",(page['run_id'],)).fetchone()[0]
    db.execute('UPDATE extraction_runs SET status=? WHERE id=?',('partial' if pending and usable else 'needs_review' if pending else 'ready',page['run_id']))
    # Index and review history commit together. API/embedding failures roll everything back.
    try:reindex_document(db,page['run_id'],prepared['policy'],prepared=prepared)
    except Exception as error:
        raise HTTPException(503,'검색 반영에 실패해 변경사항을 저장하지 않았습니다. 검색 서비스 연결을 확인한 뒤 다시 시도해 주세요.') from error
    from .knowledge_build import refresh_local_index,sync_demands,refresh_derived_for_papers
    refresh_local_index(db,[page['paper_id']])
    sync_demands(db,paper_ids=[page['paper_id']],owner_type='source_review',owner_id=page['id'],priority=20,reason='검토된 원문 변경')
    refresh_derived_for_papers(db,[page['paper_id']],priority=20)
    job_id=None
    remaining=db.execute("SELECT count(*) FROM pages pg JOIN paper_documents d ON d.current_run_id=pg.run_id WHERE d.paper_id=? AND pg.status NOT IN ('ready','excluded')",(page['paper_id'],)).fetchone()[0]
    db.execute("UPDATE processing_tasks SET wiki_job_id=?,needs_review=?,updated_at=? WHERE paper_id=? AND state='db_ready'",(job_id,int(bool(remaining)),stamp,page['paper_id']))
    db.execute("UPDATE page_review_requests SET state='applied',reviewer_id=?,decided_at=? WHERE id=?",(reviewer_id,stamp,request['id']))
    return job_id


@router.post('/page-reviews/{page_id}',status_code=201)
def submit_review(page_id:str,draft:ReviewInput):
    user=current_user()
    if not draft.confirmed:raise HTTPException(422,'원본 PDF의 해당 페이지를 확인한 뒤 체크해 주세요.')
    reason=draft.reason.strip()
    if len(reason)<3:raise HTTPException(422,'수정 또는 제외 사유를 적어 주세요.')
    can_apply=has_permission('manage_knowledge')
    request={'id':str(draft.request_id),'page_id':page_id,'revision':draft.revision,'action':draft.action,
             'proposed_text':draft.text,'reason':reason}
    def check(db):
        page=current_page(db,page_id)
        old=db.execute('SELECT * FROM page_review_requests WHERE id=?',(str(draft.request_id),)).fetchone()
        if old:
            expected=(page_id,draft.revision,draft.action,draft.text,reason,user['employee_id'])
            if tuple(old[k] for k in ('page_id','revision','action','proposed_text','reason','actor_id'))!=expected:
                raise HTTPException(409,'이미 사용한 저장 요청입니다. 최신 화면에서 다시 시도해 주세요.')
            return page,{'id':old['id'],'state':old['state']}
        if revision(db,page)!=draft.revision:raise HTTPException(409,'다른 사용자가 이 페이지를 수정했습니다. 최신 내용을 다시 열어 주세요.')
        validate_change(db,page,draft.action,draft.text)
        return page,None
    with connect() as db:
        page,old=check(db)
        if old:return old
        prepared=prepare_review_index(db,page,request) if can_apply else None
        db.execute('BEGIN IMMEDIATE')
        page,old=check(db)
        if old:return old
        db.execute("""INSERT INTO page_review_requests(id,page_id,revision,action,proposed_text,reason,actor_id,state,created_at)
                      VALUES(?,?,?,?,?,?,?,'pending',?)""",(*request.values(),user['employee_id'],now()))
        job_id=apply_change(db,page,request,user['employee_id'],prepared) if can_apply else None
        db.commit()
        return {'id':request['id'],'state':'applied' if can_apply else 'pending','wiki_job_id':job_id}


@router.post('/page-review-requests/{request_id}/decision')
def decide_review(request_id:str,draft:ReviewDecision):
    user=require_permission('manage_knowledge')
    def check(db):
        row=db.execute('SELECT * FROM page_review_requests WHERE id=?',(request_id,)).fetchone()
        if not row:raise HTTPException(404,'검토 요청을 찾을 수 없습니다.')
        if row['state']!='pending':raise HTTPException(409,'이미 처리한 요청입니다. 최신 내용을 확인해 주세요.')
        if draft.action=='reject':
            if len(draft.note.strip())<3:raise HTTPException(422,'반영하지 않는 이유를 적어 주세요.')
            return dict(row),None
        if not draft.confirmed:raise HTTPException(422,'제안된 텍스트와 원본을 대조해 주세요.')
        page=current_page(db,row['page_id'])
        if draft.revision!=revision(db,page):raise HTTPException(409,'최신 내용을 다시 확인해 주세요.')
        return dict(row),page
    with connect() as db:
        row,page=check(db)
        prepared=prepare_review_index(db,page,row) if page else None
        db.execute('BEGIN IMMEDIATE')
        row,page=check(db)
        if draft.action=='reject':
            db.execute("UPDATE page_review_requests SET state='rejected',decision_note=?,reviewer_id=?,decided_at=? WHERE id=?",(draft.note.strip(),user['employee_id'],now(),request_id))
            job_id=None
        else:
            job_id=apply_change(db,page,row,user['employee_id'],prepared)
        db.commit()
        return {'state':'applied' if draft.action=='apply' else 'rejected','wiki_job_id':job_id}
