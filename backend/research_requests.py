"""Persistent evidence-first requests. Submission performs local work only."""
import json
import time
from datetime import datetime,timezone
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .auth import current_user, principal_context, public_user
from .pilot_db import connect, now
from .wiki_store import digest, WikiError
from .wiki_api import Question, load_catalogue, recommendations
from .llm_client import CompanyLLM
from . import answer_cache
from .conversation_context import request_context, contextual_query, context_fields, has_private_context

router = APIRouter(prefix='/api/pilot/research-requests',tags=['Research requests'])


class Submission(BaseModel):
    question: str = Field(min_length=1,max_length=1000)
    expert_ids: list[str] = Field(default_factory=list,max_length=20)
    visibility: Literal['private','team'] = 'private'
    selection: Literal['auto','manual'] | None = None
    history_run_ids: list[str] = Field(default_factory=list,max_length=6)
    report_id: str | None = Field(default=None,max_length=80)
    report_section_id: str | None = Field(default=None,max_length=80)
    report_revision: int | None = Field(default=None,ge=1)


class Visibility(BaseModel):
    visibility: Literal['private','team']


def ensure_schema(db):
    answer_cache.ensure_schema(db)
    db.execute('''CREATE TABLE IF NOT EXISTS research_requests(
        id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,question TEXT NOT NULL,
        expert_ids_json TEXT NOT NULL,expert_names_json TEXT NOT NULL,
        visibility TEXT NOT NULL CHECK(visibility IN ('private','team')),
        status TEXT NOT NULL,phase TEXT NOT NULL,input_hash TEXT NOT NULL,
        local_evidence_json TEXT NOT NULL,result_json TEXT,error TEXT NOT NULL DEFAULT '',
        job_id TEXT,cache_hit INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
    if 'context_json' not in {row['name'] for row in db.execute('PRAGMA table_info(research_requests)')}:
        db.execute("ALTER TABLE research_requests ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}'")
    db.execute('CREATE INDEX IF NOT EXISTS research_owner_time ON research_requests(owner_id,created_at)')
    db.execute('''CREATE TABLE IF NOT EXISTS research_request_metrics(
        request_id TEXT PRIMARY KEY,initial_ms REAL NOT NULL,final_ms REAL,created_at TEXT NOT NULL)''')


def _decode(raw):
    row = dict(raw)
    for name in ('expert_ids','expert_names','local_evidence','result'):
        value = row.pop(name+'_json')
        row[name] = json.loads(value) if value else None
    row['cache_hit'] = bool(row['cache_hit'])
    row.update(dict(history_run_ids=[],report_id=None,report_section_id=None,report_revision=None)|json.loads(row.pop('context_json','{}')))
    row['learning_eligible']=not bool(row['history_run_ids'] or row['report_id'])
    return row


def _public(row):
    result = json.loads(json.dumps(row,ensure_ascii=False))
    result['is_owner'] = result['owner_id'] == current_user()['employee_id']
    if not result['is_owner']:
        result.update(history_run_ids=[],report_id=None,report_section_id=None,report_revision=None)
    # Shared answers expose only their explicitly shared claim snapshots, not
    # their owner's private trace, prompts, other questions, or model responses.
    answer = result.get('result')
    if answer and answer.get('run_id'):
        old = '/api/pilot/answer-runs/'+answer['run_id']+'/claims/'
        new = '/api/pilot/research-requests/'+row['id']+'/claims/'
        for part in [answer.get('synthesis',{}),*answer.get('answers',[])]:
            part['markdown'] = part.get('markdown','').replace(old,new)
    result.pop('input_hash',None)
    result.pop('job_id',None)
    return result


def _observe(db,row):
    try:
        from .knowledge_learning import observe_research_request
    except ImportError:
        return
    observe_research_request(db,row)


def _reconcile_task(db,raw):
    """Recover request state when a worker died before its handler could update it."""
    if raw is None or raw['status'] not in ('queued','running') or not raw['job_id']:
        return raw
    task = db.execute('SELECT state FROM work_tasks WHERE id=?',(raw['job_id'],)).fetchone()
    state = task['state'] if task else 'failed'
    if state == 'completed':
        state = 'completed' if raw['result_json'] else 'failed'
    if state in ('completed','failed','cancelled') or (state=='queued' and raw['status']=='running'):
        error = '답변 작업이 중단되었습니다. 다시 질문해 주세요.' if state=='failed' else ''
        db.execute('UPDATE research_requests SET status=?,phase=?,error=?,updated_at=? WHERE id=?',
                   (state,state,error,now(),raw['id']))
        return db.execute('SELECT * FROM research_requests WHERE id=?',(raw['id'],)).fetchone()
    return raw


def read_request(request_id,database=None,owner_only=False):
    with connect(database) as db:
        row = db.execute('SELECT * FROM research_requests WHERE id=?',(request_id,)).fetchone()
        if row is None or (row['owner_id'] != current_user()['employee_id'] and (owner_only or row['visibility'] != 'team')):
            raise HTTPException(404,'질문을 찾을 수 없습니다.')
        row = _reconcile_task(db,row)
        db.commit()
    return _decode(row)


def local_evidence(question,ids,database=None,conversation=()):
    from .wiki_retrieval import retrieve, keywords
    from .answer_evidence import build_evidence
    from .wiki_answer import _limitations
    store,state,catalogue = load_catalogue(database)
    ranked,notices = retrieve(question,ids,database=database,limit=60,local_only=True)
    if conversation:
        from .wiki_answer import _merge
        contextual,messages=retrieve(contextual_query(question,conversation),ids,database=database,limit=60,local_only=True)
        ranked=_merge([ranked,contextual]);notices.extend(messages)
    bundle = build_evidence(ranked,ids,store=store,state=state,catalogue=catalogue,budget=48000,database=database)
    evidence = bundle['evidence']
    claims = [ev['claim'] for ev in evidence.values() if ev['kind']=='claim']
    hits = [dict(id=key,paper_id=ev['paper_id'],title=state['papers'][ev['paper_id']]['title'],
        text=ev['text'],page_number=ev['page_number'],page_id=ev['page_id'],document_id=ev['document_id'])
        for key,ev in evidence.items() if ev['kind']=='source_text']
    return dict(claims=claims,total_claims=len(claims),hits=hits,terms=keywords(question,database),answers=[],
        note='로컬 색인에서 찾은 근거입니다. 종합 답변의 생성·내용 검토는 아직 완료되지 않았습니다.',
        synthesis=dict(markdown='답변을 준비하고 있습니다. 먼저 검색한 논문 근거를 확인하세요.',mode='evidence',wikis=[]),
        research=dict(mode='deep',queries=[question],rounds=1,paper_count=len(bundle['diagnostics']['paper_ids']),
            validation_status='not_generated',limitations=list(dict.fromkeys(_limitations(evidence,state)+notices+bundle['diagnostics']['notices']))))


def _active_request(db,owner,question,expert_ids,visibility,context):
    # An active worker reads the latest source/policy versions when it runs.
    # Sharing this very request creates a learning origin, so the evolving
    # cache fingerprint must not turn a repeated click into a second job.
    expected=answer_cache.scope_key(expert_ids)
    for row in db.execute('''SELECT * FROM research_requests
      WHERE owner_id=? AND question=? AND visibility=? AND status IN ('queued','running')
      ORDER BY created_at DESC''',(owner,question,visibility)):
        stored=dict(history_run_ids=[],report_id=None,report_section_id=None,report_revision=None)|json.loads(row['context_json'])
        if answer_cache.scope_key(json.loads(row['expert_ids_json']))==expected and stored==context:
            return row
    return None


def submit_request(body,database=None):
    started = time.perf_counter()
    from .task_queue import enqueue_task
    question = body.question.strip()
    if not question:
        raise HTTPException(422,'질문을 입력해 주세요.')
    owner = current_user()['employee_id']
    conversation=request_context(body,database)
    private_context=context_fields(body)
    # An answer can repeat private context even if its identifiers are hidden.
    # Keep the entire contextual request private, including its final result.
    visibility='private' if has_private_context(body) else body.visibility
    _,state,_ = load_catalogue(database)
    ids = list(dict.fromkeys(body.expert_ids))
    if body.selection=='manual' and not ids:
        raise HTTPException(422,'질문할 전문가를 선택해 주세요.')
    if not ids or body.selection=='auto':
        ids = [e['id'] for e in recommendations(contextual_query(question,conversation),database=database)['experts'][:2]]
    if not ids:
        raise HTTPException(422,'질문에 맞는 전문가를 찾지 못했습니다. 전문가를 직접 선택해 주세요.')
    if any(eid not in state['experts'] for eid in ids):
        raise HTTPException(409,'사용 가능한 전문가를 다시 선택해 주세요.')
    names = [state['experts'][eid]['name'] for eid in ids]
    identity = CompanyLLM(role='answer',database=database).identity
    fingerprint = answer_cache.validity(ids,database=database,model_identity=identity)
    key = digest([owner,question,sorted(ids),visibility,private_context,fingerprint])
    with connect(database) as db:
        ensure_schema(db)
        existing = _active_request(db,owner,question,ids,visibility,private_context)
    if existing:
        return _public(read_request(existing['id'],database,owner_only=True))
    preview = local_evidence(question,ids,database,conversation)
    request = Question(question=question,expert_ids=ids,**private_context)
    cached = answer_cache.reuse_answer(request,owner_id=owner,model_identity=identity,database=database)
    rid,stamp = str(uuid4()),now()
    with connect(database) as db:
        # Serialize the second dedup check with queue insertion.
        db.execute('BEGIN IMMEDIATE')
        existing = _active_request(db,owner,question,ids,visibility,private_context)
        if existing:
            return _public(_decode(existing))
        job = None if cached else enqueue_task(db,'research_answer',rid,key,{'request_id':rid},priority=0,max_attempts=2)
        db.execute('''INSERT INTO research_requests
          (id,owner_id,question,expert_ids_json,expert_names_json,visibility,status,phase,input_hash,
           local_evidence_json,result_json,error,job_id,cache_hit,created_at,updated_at,context_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (rid,owner,question,json.dumps(ids),json.dumps(names,ensure_ascii=False),visibility,
             'completed' if cached else 'queued','completed' if cached else 'queued',key,
             json.dumps(preview,ensure_ascii=False),json.dumps(cached,ensure_ascii=False) if cached else None,
             '',job,int(bool(cached)),stamp,stamp,json.dumps(private_context)))
        initial_ms = (time.perf_counter()-started)*1000
        db.execute('INSERT INTO research_request_metrics VALUES(?,?,?,?)',(rid,initial_ms,initial_ms if cached else None,stamp))
        _observe(db,_decode(db.execute('SELECT * FROM research_requests WHERE id=?',(rid,)).fetchone()))
        db.commit()
    return _public(read_request(rid,database,owner_only=True))


@router.post('')
def submit(body:Submission):
    return submit_request(body)


@router.get('')
def list_requests(visibility:Literal['private','team']|None=None,limit:int=50,mine:bool=False,summary:bool=False):
    owner = current_user()['employee_id']
    with connect() as db:
        if visibility=='team' and not mine:
            where="visibility='team'";args=()
        else:
            clause = " AND visibility='private'" if visibility=='private' else ''
            where='owner_id=?'+clause;args=(owner,)
        total=db.execute('SELECT count(*) FROM research_requests WHERE '+where,args).fetchone()[0]
        columns=('id,owner_id,question,expert_ids_json,expert_names_json,visibility,status,phase,input_hash,error,'
                 "job_id,cache_hit,created_at,updated_at,context_json,'{}' AS local_evidence_json,"
                 "CASE WHEN result_json IS NOT NULL THEN '{}' ELSE NULL END AS result_json") if summary else '*'
        rows=db.execute('SELECT '+columns+' FROM research_requests WHERE '+where+' ORDER BY created_at DESC LIMIT ?',
                        (*args,max(1,min(limit,100)))).fetchall()
        rows = [_reconcile_task(db,row) for row in rows]
        db.commit()
    if summary:
        rows=[dict(row,local_evidence_json='{}',result_json=None) for row in rows]
    result=[_public(_decode(row)) for row in rows]
    if summary:
        for row in result:
            row.pop('result',None);row.pop('local_evidence',None)
    return {'requests':result,'total':total}


@router.get('/{request_id}')
def status(request_id:str):
    return _public(read_request(request_id))


@router.post('/{request_id}/cancel')
def cancel(request_id:str):
    from .task_queue import cancel_task
    row = read_request(request_id,owner_only=True)
    with connect() as db:
        if row['status'] in ('queued','running'):
            if row['job_id']:
                cancel_task(db,row['job_id'])
            db.execute("UPDATE research_requests SET status='cancelled',phase='cancelled',updated_at=? WHERE id=? AND status IN ('queued','running')",(now(),request_id))
            db.commit()
    return _public(read_request(request_id))


@router.patch('/{request_id}/visibility')
def change_visibility(request_id:str,body:Visibility):
    row=read_request(request_id,owner_only=True)
    if body.visibility=='team' and not row['learning_eligible']:
        raise HTTPException(409,'개인 대화나 보고서를 참조한 질문은 비공개로 유지됩니다. 공유하려면 새 질문으로 조사해 주세요.')
    with connect() as db:
        db.execute('UPDATE research_requests SET visibility=?,updated_at=? WHERE id=?',(body.visibility,now(),request_id))
        _observe(db,_decode(db.execute('SELECT * FROM research_requests WHERE id=?',(request_id,)).fetchone()))
        db.commit()
    return _public(read_request(request_id))


@router.get('/{request_id}/claims/{claim_id}')
def shared_claim(request_id:str,claim_id:str):
    row = read_request(request_id)
    run_id = (row['result'] or {}).get('run_id')
    with connect() as db:
        run = db.execute('SELECT evidence_json FROM answer_runs WHERE id=? AND owner_id=?',(run_id,row['owner_id'])).fetchone()
    ev = json.loads(run[0]).get('claim:'+claim_id) if run else None
    if not ev or ev.get('kind')!='claim':
        raise HTTPException(404,'이 답변의 인용 근거를 찾을 수 없습니다.')
    return ev['claim']


def handle_request(payload,context):
    from .wiki_answer import answer
    database,rid = context.database,payload['request_id']
    with connect(database) as db:
        raw = db.execute('SELECT * FROM research_requests WHERE id=?',(rid,)).fetchone()
        if raw is None or raw['status'] in ('cancelled','completed'):
            return {'status':'cancelled' if raw is None else raw['status']}
        row = _decode(raw)
        user = db.execute('SELECT * FROM users WHERE employee_id=? AND active=1',(row['owner_id'],)).fetchone()
        if not user:
            db.execute("UPDATE research_requests SET status='failed',phase='failed',error=?,updated_at=? WHERE id=?",('질문 작성자의 계정이 활성 상태가 아닙니다.',now(),rid));db.commit()
            return {'status':'failed'}
        db.execute("UPDATE research_requests SET status='running',phase='evidence',error='',updated_at=? WHERE id=?",(now(),rid));db.commit()
    token = principal_context.set(public_user(user))
    class Progress:
        @property
        def checkpoint_data(self):
            return context.checkpoint_data
        def check_cancelled(self):
            context.check_cancelled()
        def checkpoint(self,data):
            context.checkpoint({**context.checkpoint_data,**data})
            with connect(database) as db:
                db.execute("UPDATE research_requests SET phase=?,updated_at=? WHERE id=? AND status='running'",(data.get('phase','answer'),now(),rid));db.commit()
    try:
        context.check_cancelled()
        request = Question(question=row['question'],expert_ids=row['expert_ids'],
            **{key:row[key] for key in ('history_run_ids','report_id','report_section_id','report_revision')})
        request_context(request,database)
        llm = CompanyLLM(role='answer',database=database)
        fingerprint = answer_cache.validity(request.expert_ids,database=database,model_identity=llm.identity)
        result = answer_cache.reuse_answer(request,owner_id=row['owner_id'],model_identity=llm.identity,database=database)
        cache_hit = result is not None
        if result is None:
            prepared = None if has_private_context(request) else answer_cache.read_prepared_case(request.question,request.expert_ids,row['owner_id'],database)
            result = answer(request,llm,database=database,prepared=prepared,context=Progress())
        context.check_cancelled()
        answer_cache.remember_answer(request,result,owner_id=row['owner_id'],model_identity=llm.identity,expected_hash=fingerprint,database=database)
        with connect(database) as db:
            db.execute("UPDATE research_requests SET status='completed',phase='completed',result_json=?,cache_hit=?,updated_at=? WHERE id=? AND status='running'",(json.dumps(result,ensure_ascii=False),int(cache_hit),now(),rid))
            _observe(db,_decode(db.execute('SELECT * FROM research_requests WHERE id=?',(rid,)).fetchone()))
            db.execute('UPDATE research_request_metrics SET final_ms=initial_ms+? WHERE request_id=?',
                (max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(row['created_at'])).total_seconds()*1000),rid))
            db.commit()
        return {'request_id':rid,'run_id':result['run_id'],'status':'completed'}
    except Exception as error:
        from .task_queue import WorkCancelled, get_task
        cancelled = isinstance(error,WorkCancelled)
        message = '' if cancelled else (str(error.detail) if isinstance(error,HTTPException) else str(error) if isinstance(error,WikiError) else '답변 준비 중 오류가 발생했습니다. 다시 질문해 주세요.')
        task = get_task(database,context.job_id)
        state = 'cancelled' if cancelled else 'queued' if task and task['attempts'] < task['max_attempts'] else 'failed'
        with connect(database) as db:
            db.execute("UPDATE research_requests SET status=?,phase=?,error=?,updated_at=? WHERE id=? AND status<>'cancelled'",(state,state,message,now(),rid));db.commit()
        raise
    finally:
        principal_context.reset(token)


def register_handlers():
    from .task_queue import register_handler
    register_handler('research_answer',handle_request)
