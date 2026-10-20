"""Demand-driven shared evidence: local retrieval first, bounded model stages later.

Extraction candidates never enter canonical claims. Exact source matching,
scientific syntax checks and a separate model review precede publication;
human scientific review remains pending.
Every task performs at most one remote LLM request, outside SQL transactions.
"""
from __future__ import annotations

import json
import re
from .pilot_db import connect, now
from .wiki_store import WikiStore, WikiError, digest
from .wiki_policy import load_prompt
from .llm_client import CompanyLLM, LLMError

VERSION='demand-evidence-v1'
VALIDATOR_VERSION='exact-quotes-scientific-syntax-independent-review-v3'
STATEMENT_KINDS={'observed':'관측 결과','estimated':'추정 결과','interpretation':'종합 해석','unclassified':'분류 미확인'}
SYNTAX_LIMITATION='인용문·수치·단위 및 명시 조건의 자동 구문 검사입니다. 조건의 완전성·실험 비교 가능성·과학적 타당성은 독립 내용 검토와 사람의 검토가 필요합니다.'
BOOTSTRAP_PAPERS=3
CHUNKS_PER_DEMAND=2


def ensure_schema(db):
    # execute(), not executescript(): callers may have uncommitted source changes.
    statements=(
        '''CREATE TABLE IF NOT EXISTS knowledge_bootstrap (
          key TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS knowledge_demands (
          id TEXT PRIMARY KEY, owner_type TEXT NOT NULL, owner_id TEXT NOT NULL,
          paper_id TEXT NOT NULL REFERENCES papers(id), priority INTEGER NOT NULL,
          reason TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(owner_type,owner_id,paper_id))''',
        '''CREATE TABLE IF NOT EXISTS knowledge_extracts (
          id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id),
          unit_id TEXT NOT NULL, input_hash TEXT NOT NULL, evidence_json TEXT NOT NULL,
          sections_json TEXT NOT NULL DEFAULT '[]', review_json TEXT NOT NULL DEFAULT '{}',
          state TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS knowledge_syntheses (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, expert_id TEXT,
          kind TEXT NOT NULL, paper_ids_json TEXT NOT NULL, input_hash TEXT NOT NULL,
          evidence_json TEXT NOT NULL DEFAULT '{}', sections_json TEXT NOT NULL DEFAULT '[]',
          refs_json TEXT NOT NULL DEFAULT '{}', review_json TEXT NOT NULL DEFAULT '{}',
          state TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS knowledge_synthesis_demands (
          synthesis_id TEXT NOT NULL REFERENCES knowledge_syntheses(id),
          owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY(synthesis_id,owner_type,owner_id))''',
        'CREATE INDEX IF NOT EXISTS ix_knowledge_demands_active ON knowledge_demands(paper_id,active)',
        'CREATE INDEX IF NOT EXISTS ix_knowledge_extracts_paper ON knowledge_extracts(paper_id,state)',
    )
    for sql in statements:db.execute(sql)


def bootstrap_local_state(db):
    """One local upgrade: retain corpus/history, publish FTS and register demand.

    Only exact historical automatic reasons are superseded. Explicit/admin and
    ambiguous jobs remain available to the compatible legacy queue bridge.
    No LLM/embedding request is made by this migration.
    """
    ensure_schema(db)
    if db.execute("SELECT 1 FROM knowledge_bootstrap WHERE key='demand-search-v1'").fetchone():
        return {'status':'unchanged'}
    refresh_local_index(db)
    tasks=sync_demands(db)
    converted=[]
    automatic={'papers, evidence or expert settings changed','duplicate source linked','Reviewed source page updated'}
    for row in db.execute("SELECT id,reason FROM wiki_jobs WHERE state='queued' AND scope='all' AND expert_id='' ORDER BY id").fetchall():
        if row['reason'] not in automatic and not re.fullmatch(r'Wiki writing policy checked: [0-9a-f]{12}',row['reason']):continue
        db.execute("UPDATE wiki_jobs SET state='done',finished_at=?,error=? WHERE id=? AND state='queued'",(now(),'수요 기반 지식 작업으로 전환했습니다. 원문 검색은 준비되었고 지식은 별도 구축합니다.',row['id']))
        converted.append(row['id'])
    result={'status':'initialized','superseded_automatic_jobs':converted,'tasks':tasks}
    db.execute("INSERT INTO knowledge_bootstrap VALUES('demand-search-v1',?,?)",(json.dumps(result),now()))
    return result


def refresh_local_index(db,paper_ids=None,*,wiki_ids=()):
    from .wiki_retrieval import refresh_local_index as refresh
    return refresh(db,paper_ids,wiki_ids=wiki_ids)


def rebind_source_units(db,paper_ids=None):
    """Reattach unchanged source intervals after chunk IDs change on reindex.

    Analysis identity is page/offset/text, not a document-wide chunk fingerprint.
    No generation or review is repeated solely because a different page changed.
    """
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_extracts'").fetchone():return
    allowed=set(paper_ids) if paper_ids is not None else None
    for unit in db.execute("SELECT * FROM retrieval_units WHERE kind='passage'").fetchall():
        evidence=_evidence(unit);pid=evidence['paper_id']
        if allowed is not None and pid not in allowed:continue
        eid='extract-'+digest([pid,evidence['page_id'],evidence['offset'],evidence['text']])[:28]
        db.execute('UPDATE knowledge_extracts SET unit_id=? WHERE id=?',(unit['id'],eid))


def _active_experts(db):
    return {r['id']:dict(r) for r in db.execute('''SELECT * FROM expert_categories
      WHERE id NOT IN (SELECT expert_id FROM expert_deletions)
      AND id NOT IN (SELECT expert_id FROM expert_policies WHERE enabled=0)''')}


def _evidence(row):
    meta=json.loads(row['metadata_json'])
    return dict(kind='source_text',paper_id=meta['paper_id'],page_id=meta['page_id'],
                document_id=meta['document_id'],page_number=meta['page_number'],
                offset=meta['offset'],text=row['text'])


def _source_valid(db,evidence):
    row=db.execute('''SELECT pg.text,pg.status FROM pages pg JOIN paper_documents d
      ON d.current_run_id=pg.run_id WHERE pg.id=? AND d.id=? AND d.paper_id=?''',
      (evidence['page_id'],evidence['document_id'],evidence['paper_id'])).fetchone()
    start=evidence['offset']; text=evidence['text']
    return bool(row and row['status']=='ready' and row['text'][start:start+len(text)]==text)


def _analysis_hash(evidence):
    return digest([VERSION,VALIDATOR_VERSION,evidence,load_prompt('compile').fingerprint,load_prompt('lint').fingerprint,_model_signature('background'),_model_signature('review')])


def _compiler_signature():
    return digest([VALIDATOR_VERSION,load_prompt('compile').fingerprint,load_prompt('lint').fingerprint,_model_signature('background'),_model_signature('review')])


def _model_signature(role):
    try:return CompanyLLM(role=role).identity
    except (ValueError,OSError):
        # Raw ingestion/search remains available while optional model settings
        # are being configured. A valid configuration creates a fresh task hash.
        return 'unavailable-model-configuration:'+role


def _selected_units(db,pid,question='',limit=CHUNKS_PER_DEMAND,*,remaining_only=False):
    rows=[dict(r) for r in db.execute("SELECT * FROM retrieval_units WHERE kind='passage' AND EXISTS(SELECT 1 FROM json_each(paper_ids_json) WHERE value=?) ORDER BY reference_id,id",(pid,))]
    rows=[r for r in rows if not _canonical_covers(db,r)]
    if question:
        from .wiki_retrieval import keywords
        terms=keywords(question,glossary=None,database=next(r['file'] for r in db.execute('PRAGMA database_list') if r['name']=='main'))
        rows.sort(key=lambda r:(-sum(t.casefold() in r['text'].casefold() for t in terms),r['id']))
    if remaining_only:
        existing={r['input_hash'] for r in db.execute('SELECT input_hash FROM knowledge_extracts WHERE paper_id=?',(pid,))}
        rows=[r for r in rows if _analysis_hash(_evidence(r)) not in existing]
    return rows[:limit]


def _canonical_covers(db,unit):
    """A few claims never imply coverage of the rest of a paper or paragraph."""
    evidence=_evidence(unit);start=evidence['offset'];end=start+len(evidence['text'])
    spans=db.execute('''SELECT DISTINCT s.start_offset,s.end_offset,s.quote FROM evidence_spans s
      JOIN claim_evidence ce ON ce.span_id=s.id AND ce.paper_id=s.paper_id
      WHERE s.paper_id=? AND s.page_id=? AND s.end_offset>? AND s.start_offset<? ORDER BY s.start_offset''',
      (evidence['paper_id'],evidence['page_id'],start,end)).fetchall()
    cursor=start
    page=db.execute('SELECT text FROM pages WHERE id=?',(evidence['page_id'],)).fetchone()
    for span in spans:
        if not page or page['text'][span['start_offset']:span['end_offset']]!=span['quote']:continue
        if span['start_offset']>cursor:break
        cursor=max(cursor,span['end_offset'])
        if cursor>=end:return _source_valid(db,evidence)
    return False


def _paper_jobs(db,pid,priority,question='',*,remaining_only=False):
    from .task_queue import enqueue_task
    from .wiki_engine import _full_snapshot,current
    paper=db.execute('SELECT id FROM papers WHERE id=?',(pid,)).fetchone()
    if not paper:return []
    state=_full_snapshot(connection=db)
    catalogue={r['id']:dict(r) for r in db.execute('SELECT * FROM wiki_files')}
    existing=catalogue.get('source-'+pid)
    _schedule_embeddings(db,pid,priority+10)
    deps=[]
    for unit in _selected_units(db,pid,question,remaining_only=remaining_only):
        evidence=_evidence(unit)
        input_hash=_analysis_hash(evidence)
        eid='extract-'+digest([pid,evidence['page_id'],evidence['offset'],evidence['text']])[:28]
        old=db.execute('SELECT input_hash,state FROM knowledge_extracts WHERE id=?',(eid,)).fetchone()
        if not old or old['input_hash']!=input_hash:
            db.execute('''INSERT INTO knowledge_extracts(id,paper_id,unit_id,input_hash,evidence_json,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET unit_id=excluded.unit_id,input_hash=excluded.input_hash,
              evidence_json=excluded.evidence_json,sections_json='[]',review_json='{}',state='pending',updated_at=excluded.updated_at''',
              (eid,pid,unit['id'],input_hash,json.dumps(evidence,ensure_ascii=False),now(),now()))
        else:
            db.execute('UPDATE knowledge_extracts SET unit_id=? WHERE id=?',(unit['id'],eid))
        payload={'artifact_id':eid,'input_hash':input_hash,'paper_id':pid}
        extract=enqueue_task(db,'knowledge.extract',eid,input_hash,payload,priority=priority,revive=True)
        review=enqueue_task(db,'knowledge.review',eid,input_hash,payload,priority=priority,dependencies=(extract,),revive=True)
        deps.append(review)
    # Include every reviewed/selected interval in the publication identity. A new
    # demand can extend coverage without rereading earlier intervals.
    artifact_hashes=[tuple(r) for r in db.execute('SELECT id,input_hash FROM knowledge_extracts WHERE paper_id=? ORDER BY id',(pid,))]
    publication=digest([VERSION,state['papers'][pid]['hash'],artifact_hashes])
    return [enqueue_task(db,'knowledge.publish_source',pid,publication,{'paper_id':pid,'paper_hash':state['papers'][pid]['hash']},priority=priority,dependencies=deps,revive=True)]


def _schedule_embeddings(db,pid,priority):
    from .chunking import load_chunk_policy
    from .task_queue import enqueue_task
    try:policy=load_chunk_policy()
    except (ValueError,OSError):return
    if not policy.embedding:return
    model=digest(policy.embedding.identity())
    for row in db.execute("SELECT id,input_hash FROM retrieval_units WHERE EXISTS(SELECT 1 FROM json_each(paper_ids_json) WHERE value=?) AND (model_fingerprint IS NULL OR model_fingerprint<>?)",(pid,model)).fetchall():
        enqueue_task(db,'knowledge.embed',row['id'],digest([row['input_hash'],model]),
                     {'unit_id':row['id'],'input_hash':row['input_hash'],'model':model,'paper_id':pid},priority=priority,revive=True)


def _embed(payload,context):
    from .chunking import load_chunk_policy
    from .wiki_retrieval import GatedEmbeddingClient
    from .task_queue import WorkCancelled
    context.check_cancelled();policy=load_chunk_policy()
    if not policy.embedding or digest(policy.embedding.identity())!=payload['model']:return {'status':'obsolete_model'}
    with connect(context.database) as db:
        if not _wanted(db,payload['paper_id']):raise WorkCancelled('활성 검색 수요가 없습니다.')
        row=db.execute('SELECT * FROM retrieval_units WHERE id=? AND input_hash=?',(payload['unit_id'],payload['input_hash'])).fetchone()
    if not row:return {'status':'obsolete'}
    if row['model_fingerprint']==payload['model']:return {'status':'reused'}
    vector=GatedEmbeddingClient(policy.embedding,context.database).embed([row['text']])[0]
    context.check_cancelled()
    with connect(context.database) as db:
        db.execute('UPDATE retrieval_units SET vector_json=?,model_fingerprint=? WHERE id=? AND input_hash=?',(json.dumps(vector),payload['model'],row['id'],row['input_hash']));db.commit()
    return {'status':'embedded'}


def sync_demands(db,*,expert_id=None,paper_ids=None,owner_type='expert',owner_id=None,
                 priority=40,reason='전문가 관심 범위',max_papers=BOOTSTRAP_PAPERS,question=''):
    """Register bounded source demand; shared extraction IDs deduplicate experts.

    Calling without paper_ids reconciles active expert subscriptions. Removed
    interests release demands, never archive shared content or revision history.
    Explicit paper_ids are the already-retrieved scope of a research request.
    """
    ensure_schema(db)
    experts=_active_experts(db)
    db.execute("UPDATE knowledge_demands SET active=0,updated_at=? WHERE owner_type='expert' AND owner_id NOT IN (SELECT id FROM expert_categories WHERE id NOT IN (SELECT expert_id FROM expert_deletions) AND id NOT IN (SELECT expert_id FROM expert_policies WHERE enabled=0))",(now(),))
    subscriptions=[]
    if paper_ids is not None:
        selected=sorted(set(paper_ids))
        if expert_id:
            if expert_id not in experts:return []
            allowed={r[0] for r in db.execute('SELECT paper_id FROM expert_papers WHERE expert_id=?',(expert_id,))}
            selected=[p for p in selected if p in allowed]
        subscriptions=[(owner_type,owner_id or expert_id or 'explicit',selected)]
    else:
        for eid in ([expert_id] if expert_id else sorted(experts)):
            if eid not in experts:continue
            owned=[r[0] for r in db.execute('SELECT paper_id FROM expert_papers WHERE expert_id=? ORDER BY paper_id',(eid,))]
            db.execute("UPDATE knowledge_demands SET active=0,updated_at=? WHERE owner_type='expert' AND owner_id=?",(now(),eid))
            subscriptions.append(('expert',eid,owned))
    tasks=[]
    for typ,owner,pids in subscriptions:
        for position,pid in enumerate(pids):
            if not db.execute('SELECT 1 FROM papers WHERE id=?',(pid,)).fetchone():continue
            did='demand-'+digest([typ,owner,pid])[:28]
            db.execute('''INSERT INTO knowledge_demands VALUES(?,?,?,?,?,?,1,?,?)
              ON CONFLICT(owner_type,owner_id,paper_id) DO UPDATE SET active=1,priority=min(priority,excluded.priority),reason=excluded.reason,updated_at=excluded.updated_at''',
              (did,typ,owner,pid,priority,reason,now(),now()))
            if paper_ids is not None or position<max_papers:
                tasks.extend(_paper_jobs(db,pid,priority,question))
    from .task_queue import enqueue_task
    for eid in ([expert_id] if expert_id else sorted(experts)):
        if eid not in experts:continue
        owned=[r[0] for r in db.execute('SELECT paper_id FROM expert_papers WHERE expert_id=? ORDER BY paper_id',(eid,))]
        tasks.append(enqueue_task(db,'knowledge.expert_index',eid,digest([experts[eid],owned]),{'expert_id':eid},priority=10,revive=True))
    return list(dict.fromkeys(tasks))


def enqueue_background_work(db):
    """Advance one shared paper by two intervals per idle maintenance cycle.

    The first three papers are a bootstrap, not a permanent cap. Terminal input
    hashes (including rejected candidates) are never repeatedly model-reviewed.
    Changed material/model/policy gets a new hash and can be processed again.
    """
    ensure_schema(db)
    if db.execute("SELECT 1 FROM work_tasks WHERE kind IN ('knowledge.extract','knowledge.review','knowledge.publish_source') AND state IN ('queued','running') LIMIT 1").fetchone():return []
    experts=_active_experts(db)
    candidates=[]
    for eid in sorted(experts):
        candidates.extend(r[0] for r in db.execute('SELECT paper_id FROM expert_papers WHERE expert_id=? ORDER BY paper_id',(eid,)))
    for pid in dict.fromkeys(candidates):
        if not _wanted(db,pid):continue
        if _selected_units(db,pid,remaining_only=True):
            return _paper_jobs(db,pid,50,remaining_only=True)
    return []


def coverage_status(db,*,paper_ids=None,expert_id=None):
    ensure_schema(db)
    ids=set(paper_ids) if paper_ids is not None else ({r[0] for r in db.execute('SELECT paper_id FROM expert_papers WHERE expert_id=?',(expert_id,))} if expert_id else {r[0] for r in db.execute('SELECT id FROM papers')})
    from .wiki_engine import _full_snapshot,current
    state=_full_snapshot(connection=db); catalogue={r['id']:dict(r) for r in db.execute('SELECT * FROM wiki_files')}
    rows=[]
    for pid in sorted(ids):
        source_units=[dict(r) for r in db.execute("SELECT * FROM retrieval_units WHERE kind='passage' AND EXISTS(SELECT 1 FROM json_each(paper_ids_json) WHERE value=?)",(pid,))]
        units={r['id'] for r in source_units};canonical={r['id'] for r in source_units if _canonical_covers(db,r)}
        approved={r['unit_id'] for r in db.execute("SELECT * FROM knowledge_extracts WHERE paper_id=? AND state='approved'",(pid,)) if _source_valid(db,json.loads(r['evidence_json'])) and r['input_hash']==_analysis_hash(json.loads(r['evidence_json']))}
        root=catalogue.get('source-evidence-'+pid) or catalogue.get('source-'+pid); ready=bool(root and root['status']=='ready' and current(root,state,catalogue))
        refs=json.loads(root['refs_json']) if ready else {}
        coverage=refs.get('coverage',{})
        complete_intervals=bool(units) and units<=approved|canonical
        status='ready' if ready and complete_intervals and not coverage.get('partial',True) else 'partial' if ready or approved or canonical else 'pending'
        rows.append(dict(paper_id=pid,status=status,ready_chunks=len((approved&units)|canonical),total_chunks=len(units),
                         review_state='model_reviewed_human_pending' if approved else 'curated_evidence' if ready else 'pending',coverage=coverage))
    return dict(total=len(rows),ready=sum(r['status']=='ready' for r in rows),partial=sum(r['status']=='partial' for r in rows),pending=sum(r['status']=='pending' for r in rows),papers=rows)


def _wanted(db,pid):
    for row in db.execute('SELECT owner_type,owner_id FROM knowledge_demands WHERE paper_id=? AND active=1',(pid,)):
        if _owner_active(db,row['owner_type'],row['owner_id']):return True
    return False


def _owner_active(db,kind,owner):
    if kind=='expert':return owner in _active_experts(db)
    if kind=='learning':
        return bool(db.execute("SELECT 1 FROM learning_questions q WHERE q.id=? AND q.state<>'inactive' AND EXISTS(SELECT 1 FROM learning_origins o WHERE o.question_id=q.id AND o.active=1)",(owner,)).fetchone())
    if kind=='promotion':
        original=db.execute("SELECT expert_id FROM knowledge_syntheses WHERE id=? AND kind='specialization'",(owner,)).fetchone()
        if not original or original['expert_id'] not in _active_experts(db):return False
        return any(_owner_active(db,r['owner_type'],r['owner_id']) for r in db.execute("SELECT owner_type,owner_id FROM knowledge_synthesis_demands WHERE synthesis_id=? AND active=1 AND owner_type IN ('expert','learning','manual_shared')",(owner,)))
    return True


def release_owner_demands(db,owner_type,owner_id,*,withdraw_shared=False):
    """Release interests; explicit sharing withdrawal also hides orphan topics.

    Shared source evidence and immutable Markdown history are always retained.
    Ordinary expert membership changes never archive shared derived content.
    """
    ensure_schema(db)
    ids=[r[0] for r in db.execute('SELECT synthesis_id FROM knowledge_synthesis_demands WHERE owner_type=? AND owner_id=?',(owner_type,owner_id))]
    db.execute('UPDATE knowledge_demands SET active=0,updated_at=? WHERE owner_type=? AND owner_id=?',(now(),owner_type,owner_id))
    db.execute('UPDATE knowledge_synthesis_demands SET active=0 WHERE owner_type=? AND owner_id=?',(owner_type,owner_id))
    if withdraw_shared:
        for sid in ids:
            if any(_owner_active(db,r['owner_type'],r['owner_id']) for r in db.execute('SELECT owner_type,owner_id FROM knowledge_synthesis_demands WHERE synthesis_id=? AND active=1',(sid,))):continue
            db.execute("UPDATE wiki_files SET status='archived' WHERE id=?",(sid,))
            db.execute('DELETE FROM wiki_fts WHERE page_id=?',(sid,))
            for row in db.execute("SELECT id FROM retrieval_units WHERE kind='wiki' AND reference_id=?",(sid,)).fetchall():
                db.execute('DELETE FROM retrieval_fts WHERE unit_id=?',(row['id'],))
                db.execute('DELETE FROM retrieval_units WHERE id=?',(row['id'],))
            # A common promotion keeps its originating sharing permission.
            if owner_type!='promotion':release_owner_demands(db,'promotion',sid,withdraw_shared=True)


def _load_extract(payload,context):
    context.check_cancelled()
    with connect(context.database) as db:
        row=db.execute('SELECT * FROM knowledge_extracts WHERE id=?',(payload['artifact_id'],)).fetchone()
        if not row or row['input_hash']!=payload['input_hash']:return None
        result=dict(row); evidence=json.loads(row['evidence_json'])
        if row['input_hash']!=_analysis_hash(evidence):return None
        if not _wanted(db,row['paper_id']):
            from .task_queue import WorkCancelled
            raise WorkCancelled('해당 근거에 대한 활성 수요가 없습니다.')
        if not _source_valid(db,evidence):return None
    result['evidence']={row['id']:evidence}
    return result


def _validate_candidate(reply,evidence,*,allow_empty=False):
    """Preserve epistemic labels and check only each statement's cited source.

    The schema check establishes exact quotations. Syntax checks cannot prove
    scientific truth; an independent review remains a separate required stage.
    """
    from .wiki_engine import validate_sections
    sections=validate_sections(reply,evidence,allow_empty=allow_empty)
    for raw_section,section in zip(reply['sections'],sections):
        # A heading is navigation, not an unchecked model conclusion.
        section['heading']='근거와 해석'
        for raw,item in zip(raw_section['statements'],section['statements']):
            kind=raw.get('kind','unclassified')
            if not isinstance(kind,str) or kind not in STATEMENT_KINDS:raise WikiError('관측·추정·해석 분류를 확인해야 합니다.')
            item['kind']=kind
    _validate_scientific_syntax(sections,evidence)
    return sections


def _validate_scientific_syntax(sections,evidence):
    from .answer_validation import validate_source_statement
    for section in sections:
        for item in section['statements']:
            if item.get('kind','unclassified') not in STATEMENT_KINDS:raise WikiError('관측·추정·해석 분류를 확인해야 합니다.')
            sources=[s['quote'] for s in item['source_spans']]
            for key in item['evidence_ids']:
                ev=evidence[key]
                if ev['kind']=='wiki':sources.extend(ev.get('primary_sources',[]))
            validate_source_statement(item['text'],sources)


def _classification_instruction():
    return '\n각 statement에는 kind를 observed(실험 관측), estimated(모델/계산 추정), interpretation(종합 해석), unclassified(분류 미확인) 중 하나로 명시하세요. 원문이 추정한 결과를 관측값으로 바꾸지 마세요.'


def _complete(llm,system,payload):
    """Respect the current tokenizer/context/output contract in every new stage."""
    from .wiki_budget import budget_for
    from .llm_client import CompanyLLM as ProductionLLM
    budget=budget_for(llm)
    if not budget.fits(system,payload):
        raise WikiError('선별 지식 요청이 모델 입력 한도를 초과했습니다. 근거 범위를 줄이거나 문맥 한도 설정을 확인하세요.')
    if isinstance(llm,ProductionLLM):return llm.complete_json(system,payload,max_output_tokens=budget.output_tokens)
    return llm.complete_json(system,payload)


def _extract(payload,context):
    row=_load_extract(payload,context)
    if not row:return {'status':'obsolete_or_no_demand'}
    if row['state'] in ('extracted','approved','rejected'):return {'status':row['state']}
    llm=CompanyLLM(role='background',database=context.database)
    reply=_complete(llm,load_prompt('compile').text+_classification_instruction(),dict(title='원문 구간 근거 추출',page_kind='source',evidence=row['evidence'],
        coverage={'complete_paper_review':False,'scientific_review':'pending','instruction':'제공된 구간만 분석하고 없는 조건·수치를 보충하지 마세요. 유효 진술이 없으면 sections=[]를 반환하세요.'}))
    sections=_validate_candidate(reply,row['evidence'],allow_empty=True)
    context.check_cancelled()
    with connect(context.database) as db:
        if not _source_valid(db,next(iter(row['evidence'].values()))):return {'status':'obsolete'}
        db.execute("UPDATE knowledge_extracts SET sections_json=?,state='extracted',updated_at=? WHERE id=? AND input_hash=?",(json.dumps(sections,ensure_ascii=False),now(),row['id'],row['input_hash']));db.commit()
    context.checkpoint({'artifact_id':row['id'],'state':'extracted'})
    return {'status':'extracted','statements':sum(len(s['statements']) for s in sections)}


def _review_reply(reply):
    if not isinstance(reply,dict) or type(reply.get('approved')) is not bool or not isinstance(reply.get('issues'),list) or len(reply['issues'])>20 or any(not isinstance(s,str) or len(s)>2000 for s in reply['issues']):
        raise WikiError('근거 검토 응답 형식이 올바르지 않습니다.')
    if reply['approved'] and reply['issues']:raise WikiError('검토 문제가 남아 있는 근거는 승인할 수 없습니다.')
    reusable=reply.get('common_reuse_approved',False)
    title=reply.get('common_title','')
    if type(reusable) is not bool or not isinstance(title,str) or len(title)>500 or reusable and not title.strip():
        raise WikiError('공통 지식 승격 검토 형식이 올바르지 않습니다.')
    return {'approved':reply['approved'],'issues':reply['issues'],'human_review':'pending',
            'common_reuse_approved':reply['approved'] and reusable,'common_title':title.strip() if reusable else ''}


def _review(payload,context):
    row=_load_extract(payload,context)
    if not row:return {'status':'obsolete_or_no_demand'}
    if row['state'] in ('approved','rejected'):return {'status':row['state']}
    if row['state']!='extracted':raise WikiError('추출 결과가 아직 준비되지 않았습니다.')
    sections=json.loads(row['sections_json'])
    _validate_scientific_syntax(sections,row['evidence'])
    llm=CompanyLLM(role='review',database=context.database)
    result=_review_reply(_complete(llm,load_prompt('lint').text+'\n독립 근거 검토입니다. 아래 candidate의 각 진술이 제공된 원문으로 지지되는지, 수치·조건·해석의 비약 여부를 확인하세요. JSON {"approved": boolean, "issues": [string]}만 반환하세요. 원문에 없는 주장이나 불확실한 지지는 승인하지 마세요.',dict(candidate=sections,evidence=row['evidence'])))
    context.check_cancelled()
    with connect(context.database) as db:
        if not _source_valid(db,next(iter(row['evidence'].values()))):return {'status':'obsolete'}
        db.execute('UPDATE knowledge_extracts SET review_json=?,state=?,updated_at=? WHERE id=? AND input_hash=?',(json.dumps(result,ensure_ascii=False),'approved' if result['approved'] else 'rejected',now(),row['id'],row['input_hash']));db.commit()
    return {'status':'approved' if result['approved'] else 'rejected','artifact_id':row['id']}


def _publish_source(payload,context):
    from .wiki_engine import snapshot,base_refs,source_page,escaped,render_sections,current
    context.check_cancelled()
    state=snapshot(context.database); pid=payload['paper_id']; paper=state['papers'].get(pid)
    if not paper or paper['hash']!=payload['paper_hash']:return {'status':'obsolete'}
    with connect(context.database) as db:
        if not _wanted(db,pid):
            from .task_queue import WorkCancelled
            raise WorkCancelled('해당 근거에 대한 활성 수요가 없습니다.')
        units=[dict(r) for r in db.execute("SELECT * FROM retrieval_units WHERE kind='passage' AND EXISTS(SELECT 1 FROM json_each(paper_ids_json) WHERE value=?)",(pid,))]
        unit_ids={r['id'] for r in units}
        approved=[dict(r) for r in db.execute("SELECT * FROM knowledge_extracts WHERE paper_id=? AND state='approved' ORDER BY id",(pid,)) if r['unit_id'] in unit_ids and _source_valid(db,json.loads(r['evidence_json'])) and r['input_hash']==_analysis_hash(json.loads(r['evidence_json']))]
        total=len(units);canonical_units={r['id'] for r in units if _canonical_covers(db,r)}
        covered_units=canonical_units|{r['unit_id'] for r in approved}
    store=WikiStore(context.database)
    validation_signature=_compiler_signature()
    def guard(db):
        from .wiki_engine import _full_snapshot
        from .task_queue import WorkCancelled
        context.check_cancelled()
        if not _wanted(db,pid) or _full_snapshot(connection=db)['papers'].get(pid,{}).get('hash')!=payload['paper_hash']:
            raise WorkCancelled('근거 또는 활성 수요가 변경되었습니다.')
        if validation_signature!=_compiler_signature():raise WorkCancelled('근거 검토 정책이 변경되었습니다.')
        for candidate in approved:
            live=db.execute('SELECT input_hash,sections_json,review_json,state FROM knowledge_extracts WHERE id=?',(candidate['id'],)).fetchone()
            if not live or live['state']!='approved' or any(live[key]!=candidate[key] for key in ('input_hash','sections_json','review_json')):
                raise WorkCancelled('검토된 원천 후보가 변경되었습니다.')
    existing=store.record('source-'+pid);published=[]
    if paper['claims']:
        # A DB-derived page can be regenerated; adopted/manual Markdown remains
        # intact. New raw analysis is a separate shared supplement in that case.
        if not existing or existing['producer'] in ('curated-db-migration','demand-extraction-v1'):
            existing=source_page(store,state,paper,None,'요청된 canonical 근거 재사용',guard=guard)[-1]
            published.append(existing)
    if approved:
        evidence={r['id']:json.loads(r['evidence_json']) for r in approved}; sections=[s for r in approved for s in json.loads(r['sections_json'])]
        _validate_scientific_syntax(sections,evidence)
        if not sections and not published:return {'status':'no_approved_evidence'}
        refs=base_refs([pid],state);refs['source_spans']=[span for s in sections for item in s['statements'] for span in item['source_spans']]
        refs['validation_signature']=validation_signature
        refs['coverage']=dict(partial=len(covered_units)<total,reviewed_chunks=len({r['unit_id'] for r in approved}),canonical_reused_chunks=len(canonical_units),total_chunks=total,
            selected_by='전문가 초기 범위 또는 질의 관련 원문 구간',complete_paper_review=False,scientific_review='human_pending',model_review='approved',automatic_checks='exact_quote_numeric_unit_explicit_condition_syntax',validation_limitations=SYNTAX_LIMITATION,
            intervals=[dict(page_id=e['page_id'],start=e['offset'],end=e['offset']+len(e['text'])) for e in evidence.values()])
        supplemental=bool(existing and not existing['producer'].startswith('demand-'))
        page_id=('source-evidence-' if supplemental else 'source-')+pid
        path=f'common/sources/{pid}'+('-evidence' if supplemental else '')+'.md'
        body='# '+escaped(paper['title'])+'\n\n'+f'선별 원문 구간 {len(approved)}/{total}개를 추출·별도 모델 검토했습니다. 논문 전체 검토가 아니며 사람의 과학적 검토는 대기 중입니다.\n\n'+SYNTAX_LIMITATION+'\n\n'+render_sections(sections,evidence,path)
        context.check_cancelled()
        row=store.publish(page_id,path,paper['title']+(' · 추가 원문 근거' if supplemental else ''),body,refs,digest([VERSION,paper['hash'],[(r['id'],r['input_hash'],r['sections_json'],r['review_json']) for r in approved]]),kind='source',producer='demand-extraction-v1',reason='요청된 검토 근거 갱신',guard=guard)
        published.append(row)
    if not published:return {'status':'no_approved_evidence'}
    with connect(context.database) as db:
        refresh_local_index(db,[],wiki_ids=[r['id'] for r in published]);db.commit()
    # Rebuild demanded syntheses when this incremental paper pass has settled,
    # not after every two-interval partial publication.
    with connect(context.database) as db:
        current_units={r[0] for r in db.execute("SELECT id FROM retrieval_units WHERE kind='passage' AND EXISTS(SELECT 1 FROM json_each(paper_ids_json) WHERE value=?)",(pid,))}
        unsettled=any(r['unit_id'] in current_units and _source_valid(db,json.loads(r['evidence_json'])) and r['input_hash']==_analysis_hash(json.loads(r['evidence_json']))
            for r in db.execute("SELECT * FROM knowledge_extracts WHERE paper_id=? AND state IN ('pending','extracted')",(pid,)))
        if not unsettled and not _selected_units(db,pid,remaining_only=True):
            refresh_derived_for_papers(db,[pid],priority=20)
        db.commit()
    _refresh_owners(context,[pid])
    return {'status':'published','wiki_id':published[-1]['id'],'revision':published[-1]['revision']}


def _refresh_navigation(database):
    from .wiki_engine import snapshot,current
    store=WikiStore(database);state=snapshot(database);catalogue={r['id']:r for r in store.rows()}
    store.navigation([r for r in catalogue.values() if current(r,state,catalogue)])


def _refresh_owners(context,paper_ids):
    with connect(context.database) as db:
        experts=_active_experts(db)
        owners={r['expert_id'] for r in db.execute('SELECT expert_id,paper_id FROM expert_papers') if r['paper_id'] in paper_ids and r['expert_id'] in experts}
    for eid in sorted(owners):_expert_index({'expert_id':eid},context)
    _refresh_navigation(context.database)


def _expert_index(payload,context):
    from .wiki_engine import snapshot,current,merge_refs,escaped,link_to
    context.check_cancelled();state=snapshot(context.database);eid=payload['expert_id']; expert=state['experts'].get(eid)
    if not expert:return {'status':'inactive'}
    store=WikiStore(context.database);catalogue={r['id']:r for r in store.rows()};owned=set(expert['papers'])
    pages=[r for r in catalogue.values() if r['kind'] in ('source','topic','specialization') and r['expert_id'] in (None,eid) and set(json.loads(r['refs_json'])['paper_ids']).issubset(owned) and current(r,state,catalogue)]
    refs=merge_refs(pages,state,eid);path=f'experts/{eid}/index.md'; title=expert['name']+' 전문가 Wiki'
    body='# '+escaped(title)+'\n\n'+escaped(expert['overview'])+'\n\n'+f'담당 논문 {len(owned)}편 · 현재 재사용 가능한 지식 {len(pages)}개. 원문 검색은 Wiki 완성과 독립적으로 사용할 수 있습니다.\n\n'
    body+='\n'.join(f'- [{escaped(r["title"])}]({link_to(path,r["path"])})' for r in pages)
    store.publish('expert-index-'+eid,path,title,body,refs,digest([VERSION,expert['hash'],refs,body]),scope='expert',expert_id=eid,kind='expert_index',producer='demand-navigation-v1',status='ready' if owned else 'empty')
    _refresh_navigation(context.database)
    return {'status':'linked','pages':len(pages)}


def request_synthesis(db,*,title,paper_ids,expert_id=None,kind='topic',owner_type='research',owner_id,priority=50):
    from .task_queue import enqueue_task
    if kind not in ('topic','specialization','dossier'):raise ValueError('Unsupported synthesis kind')
    if not title.strip() or len(title)>500:raise ValueError('Invalid synthesis title')
    ids=sorted(set(paper_ids))
    if not ids or len(ids)>6:raise ValueError('A synthesis requires 1–6 selected papers')
    if expert_id:
        if expert_id not in _active_experts(db):raise ValueError('Inactive expert')
        owned={r[0] for r in db.execute('SELECT paper_id FROM expert_papers WHERE expert_id=?',(expert_id,))}
        if not set(ids)<=owned:raise ValueError('Synthesis outside expert scope')
    deps=sync_demands(db,expert_id=expert_id,paper_ids=ids,owner_type=owner_type,owner_id=owner_id,priority=priority,reason=title,question=title)
    from .wiki_engine import _full_snapshot
    state=_full_snapshot(connection=db)
    hashes={pid:state['papers'][pid]['hash'] for pid in ids}
    # Common question/dossier content is shared across requesting owners. A true
    # specialization remains scoped to its expert's profile and membership.
    scoped=expert_id if kind=='specialization' else None
    sid='demand-topic-'+digest([' '.join(title.split()),ids,scoped])[:28]
    source_versions=[tuple(r) for pid in ids for r in db.execute('SELECT id,content_hash FROM wiki_files WHERE id IN (?,?) ORDER BY id',('source-'+pid,'source-evidence-'+pid))]
    selected_versions=[tuple(r) for pid in ids for r in db.execute('SELECT id,input_hash FROM knowledge_extracts WHERE paper_id=? ORDER BY id',(pid,))]
    input_hash=digest([VERSION,hashes,title,source_versions,selected_versions,_compiler_signature(),state['experts'][expert_id]['hash'] if scoped else ''])
    stamp=now()
    db.execute('''INSERT INTO knowledge_syntheses(id,title,expert_id,kind,paper_ids_json,input_hash,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET input_hash=excluded.input_hash,
      state=CASE WHEN input_hash=excluded.input_hash THEN state ELSE 'pending' END,updated_at=excluded.updated_at''',
      (sid,title,scoped,'specialization' if scoped else 'topic',json.dumps(ids),input_hash,stamp,stamp))
    db.execute('INSERT INTO knowledge_synthesis_demands VALUES(?,?,?,1) ON CONFLICT(synthesis_id,owner_type,owner_id) DO UPDATE SET active=1',(sid,owner_type,owner_id))
    payload={'artifact_id':sid,'input_hash':input_hash,'paper_hashes':hashes,'compiler_signature':_compiler_signature()}
    compile_id=enqueue_task(db,'knowledge.synthesize',sid,input_hash,payload,priority=priority,dependencies=deps,revive=True)
    review_id=enqueue_task(db,'knowledge.review_synthesis',sid,input_hash,payload,priority=priority,dependencies=(compile_id,),revive=True)
    return enqueue_task(db,'knowledge.publish_synthesis',sid,input_hash,payload,priority=priority,dependencies=(review_id,),revive=True)


def refresh_derived_for_papers(db,paper_ids,*,priority=20):
    """Refresh only actively requested syntheses affected by these source papers."""
    ensure_schema(db);changed=set(paper_ids);tasks=[]
    for row in db.execute('SELECT * FROM knowledge_syntheses ORDER BY id').fetchall():
        papers=json.loads(row['paper_ids_json'])
        if not changed.intersection(papers):continue
        owners=[dict(r) for r in db.execute('SELECT owner_type,owner_id FROM knowledge_synthesis_demands WHERE synthesis_id=? AND active=1',(row['id'],))]
        owner=next((r for r in owners if _owner_active(db,r['owner_type'],r['owner_id'])),None)
        if not owner:continue
        if row['expert_id'] and row['expert_id'] not in _active_experts(db):continue
        tasks.append(request_synthesis(db,title=row['title'],paper_ids=papers,expert_id=row['expert_id'],kind=row['kind'],
            owner_type=owner['owner_type'],owner_id=owner['owner_id'],priority=priority))
    return tasks


def refresh_expert_syntheses(db,expert_id=None,*,priority=20):
    """A changed profile/membership refreshes only already requested scoped notes.

    Out-of-scope or inactive notes retain their history without model work. The
    request path is idempotent, and shares existing source extraction stages.
    """
    from .wiki_engine import _full_snapshot,current
    ensure_schema(db)
    state=_full_snapshot(connection=db)
    catalogue={r['id']:dict(r) for r in db.execute('SELECT * FROM wiki_files')}
    tasks=[]
    for row in db.execute("SELECT * FROM knowledge_syntheses WHERE kind='specialization' ORDER BY id").fetchall():
        eid=row['expert_id']
        if expert_id is not None and eid!=expert_id:continue
        papers=json.loads(row['paper_ids_json'])
        if eid not in state['experts'] or not set(papers)<=set(state['experts'][eid]['papers']):continue
        published=catalogue.get(row['id'])
        if published and current(published,state,catalogue):continue
        owners=db.execute('SELECT owner_type,owner_id FROM knowledge_synthesis_demands WHERE synthesis_id=? AND active=1',(row['id'],)).fetchall()
        owner=next((r for r in owners if _owner_active(db,r['owner_type'],r['owner_id'])),None)
        if owner:
            tasks.append(request_synthesis(db,title=row['title'],paper_ids=papers,expert_id=eid,kind='specialization',
                owner_type=owner['owner_type'],owner_id=owner['owner_id'],priority=priority))
    return tasks


def _load_synthesis(payload,context):
    from .wiki_engine import snapshot
    context.check_cancelled();state=snapshot(context.database)
    if payload.get('compiler_signature')!=_compiler_signature():return None
    if any(state['papers'].get(pid,{}).get('hash')!=h for pid,h in payload['paper_hashes'].items()):return None
    with connect(context.database) as db:
        row=db.execute('SELECT * FROM knowledge_syntheses WHERE id=? AND input_hash=?',(payload['artifact_id'],payload['input_hash'])).fetchone()
        if not row:return None
        if not any(_owner_active(db,r['owner_type'],r['owner_id']) for r in db.execute('SELECT owner_type,owner_id FROM knowledge_synthesis_demands WHERE synthesis_id=? AND active=1',(row['id'],))):
            from .task_queue import WorkCancelled
            raise WorkCancelled('종합 요청의 활성 공유 수요가 없습니다.')
        if any(not _wanted(db,pid) for pid in json.loads(row['paper_ids_json'])):
            from .task_queue import WorkCancelled
            raise WorkCancelled('종합 근거에 대한 활성 수요가 없습니다.')
    if row['expert_id'] and (row['expert_id'] not in state['experts'] or not set(json.loads(row['paper_ids_json']))<=set(state['experts'][row['expert_id']]['papers'])):return None
    return dict(row)


def _synthesize(payload,context):
    from .wiki_engine import snapshot,current,merge_refs
    from .answer_validation import source_text
    row=_load_synthesis(payload,context)
    if not row:return {'status':'obsolete_or_no_demand'}
    if row['state'] in ('extracted','approved','rejected','published'):return {'status':row['state']}
    store=WikiStore(context.database);state=snapshot(context.database);catalogue={r['id']:r for r in store.rows()};sources=[];evidence={}
    for pid in json.loads(row['paper_ids_json']):
        selected=[catalogue[key] for key in ('source-'+pid,'source-evidence-'+pid) if key in catalogue and catalogue[key]['status']=='ready' and current(catalogue[key],state,catalogue)]
        if not selected:raise WikiError('검토된 공통 근거가 아직 준비되지 않았습니다.')
        for source in selected:
            _,body=store.read(source);sources.append(source)
            source_refs=json.loads(source['refs_json'])
            primary=[s['quote'] for s in source_refs.get('source_spans',[])]
            wanted_claims=set(source_refs.get('claim_ids',[]))
            primary.extend(source_text({'kind':'claim','claim':claim}) for paper_id in source_refs.get('paper_ids',[]) for claim in state['papers'][paper_id]['claims'] if claim['id'] in wanted_claims)
            evidence[source['id']]=dict(kind='wiki',title=source['title'],path=source['path'],text=body,primary_sources=list(dict.fromkeys(primary)),coverage=source_refs.get('coverage',{'complete_paper_review':False,'scientific_review':'pending','partial':True}))
    if len(json.dumps(evidence,ensure_ascii=False))>60000:raise WikiError('선별 근거가 큽니다. 주제를 나누어 요청하세요.')
    refs=merge_refs(sources,state,row['expert_id']);refs['validation_signature']=_compiler_signature();refs['coverage']={'complete_paper_review':False,'scientific_review':'human_pending','partial':any(e['coverage'].get('partial',True) for e in evidence.values()),'automatic_checks':'exact_quote_numeric_unit_explicit_condition_syntax','validation_limitations':SYNTAX_LIMITATION}
    llm=CompanyLLM(role='background',database=context.database)
    reply=_complete(llm,load_prompt('compile').text+_classification_instruction(),dict(title=row['title'],page_kind=row['kind'],evidence=evidence,coverage=refs['coverage']))
    sections=_validate_candidate(reply,evidence)
    context.check_cancelled()
    with connect(context.database) as db:
        db.execute("UPDATE knowledge_syntheses SET evidence_json=?,sections_json=?,refs_json=?,state='extracted',updated_at=? WHERE id=? AND input_hash=?",(json.dumps(evidence,ensure_ascii=False),json.dumps(sections,ensure_ascii=False),json.dumps(refs,ensure_ascii=False),now(),row['id'],row['input_hash']));db.commit()
    return {'status':'extracted'}


def _review_synthesis(payload,context):
    row=_load_synthesis(payload,context)
    if not row:return {'status':'obsolete_or_no_demand'}
    if row['state'] in ('approved','rejected','published'):return {'status':row['state']}
    if row['state']!='extracted':raise WikiError('종합 후보가 아직 준비되지 않았습니다.')
    _validate_scientific_syntax(json.loads(row['sections_json']),json.loads(row['evidence_json']))
    llm=CompanyLLM(role='review',database=context.database)
    result=_review_reply(_complete(llm,load_prompt('lint').text+'\n독립 종합 검토입니다. candidate가 evidence의 범위·한계·반례를 왜곡하거나 미검토 부분을 확정하지 않는지 검토하세요. JSON {"approved":boolean,"issues":[string],"common_reuse_approved":boolean,"common_title":string}만 반환하세요. common_reuse_approved는 특화 노트가 팀 내부 가정·개인 질문·전문가 개요 없이 원천 근거만으로 성립하는 공통 개념일 때만 true로 하세요. 그 외에는 false, common_title은 빈 문자열입니다. 승격 제목에도 팀·개인 정보나 검증되지 않은 내용을 넣지 마세요.',dict(page_kind=row['kind'],candidate=json.loads(row['sections_json']),evidence=json.loads(row['evidence_json']))))
    context.check_cancelled()
    with connect(context.database) as db:
        db.execute('UPDATE knowledge_syntheses SET review_json=?,state=?,updated_at=? WHERE id=? AND input_hash=?',(json.dumps(result,ensure_ascii=False),'approved' if result['approved'] else 'rejected',now(),row['id'],row['input_hash']));db.commit()
    return {'status':'approved' if result['approved'] else 'rejected'}


def _publish_synthesis(payload,context):
    from .wiki_engine import escaped,render_sections,current,snapshot
    row=_load_synthesis(payload,context)
    if not row or row['state'] not in ('approved','published'):return {'status':'not_approved'}
    _validate_scientific_syntax(json.loads(row['sections_json']),json.loads(row['evidence_json']))
    store=WikiStore(context.database);refs=json.loads(row['refs_json']);catalogue={r['id']:r for r in store.rows()};state=snapshot(context.database)
    check=dict(id=row['id'],status='ready',expert_id=row['expert_id'],refs_json=row['refs_json'])
    if not current(check,state,catalogue):return {'status':'obsolete_dependencies'}
    path=f'experts/{row["expert_id"]}/notes/{row["id"]}.md' if row['expert_id'] else f'common/topics/{row["id"]}.md'
    body='# '+escaped(row['title'])+'\n\n선별 근거의 종합을 별도 모델이 검토했습니다. 원문 전체 검토 및 사람의 과학적 검토는 완료되지 않았습니다.\n\n'+SYNTAX_LIMITATION+'\n\n'+render_sections(json.loads(row['sections_json']),json.loads(row['evidence_json']),path)
    def guard(db):
        from .wiki_engine import _full_snapshot
        from .task_queue import WorkCancelled
        context.check_cancelled()
        owners=list(db.execute('SELECT owner_type,owner_id FROM knowledge_synthesis_demands WHERE synthesis_id=? AND active=1',(row['id'],)))
        if not any(_owner_active(db,r['owner_type'],r['owner_id']) for r in owners):raise WorkCancelled('공유 수요가 철회되었습니다.')
        candidate=db.execute('SELECT input_hash FROM knowledge_syntheses WHERE id=?',(row['id'],)).fetchone()
        if not candidate or candidate['input_hash']!=row['input_hash'] or payload.get('compiler_signature')!=_compiler_signature():raise WorkCancelled('종합 후보 또는 검토 정책이 변경되었습니다.')
        live={r['id']:dict(r) for r in db.execute('SELECT * FROM wiki_files')}
        if not current(check,_full_snapshot(connection=db),live):raise WorkCancelled('종합 근거가 변경되었습니다.')
    context.check_cancelled()
    published=store.publish(row['id'],path,row['title'],body,refs,digest([row['input_hash'],row['sections_json'],row['refs_json'],row['review_json']]),scope='expert' if row['expert_id'] else 'common',expert_id=row['expert_id'],kind=row['kind'],producer='demand-synthesis-v1',guard=guard)
    with connect(context.database) as db:
        refresh_local_index(db,[],wiki_ids=[published['id']]);db.execute("UPDATE knowledge_syntheses SET state='published',updated_at=? WHERE id=? AND input_hash=?",(now(),row['id'],row['input_hash']))
        review=json.loads(row['review_json']);promotion=None
        if row['kind']=='specialization' and review.get('common_reuse_approved') is True and _owner_active(db,'promotion',row['id']):
            promotion=request_synthesis(db,title=review['common_title'],paper_ids=json.loads(row['paper_ids_json']),kind='topic',
                owner_type='promotion',owner_id=row['id'],priority=50)
        db.commit()
    _refresh_owners(context,json.loads(row['paper_ids_json']))
    return {'status':'published','wiki_id':published['id'],'revision':published['revision'],'common_promotion_task':promotion}


def register_handlers():
    from .task_queue import register_handler
    for name,handler in {'embed':_embed,'extract':_extract,'review':_review,'publish_source':_publish_source,'expert_index':_expert_index,
                         'synthesize':_synthesize,'review_synthesis':_review_synthesis,'publish_synthesis':_publish_synthesis}.items():
        register_handler('knowledge.'+name,handler)
