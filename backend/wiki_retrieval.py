"""One shared, derived retrieval index for Markdown, canonical claims and passages.

The local SQLite vector path uses exact cosine over the selected expert's scope.
It is intentionally replaceable by an ANN store when measured corpus size warrants it.
"""
import json
import math
import re
import sqlite3
import time
from .pilot_db import connect
from .terminology import query_rules
from .wiki_store import digest, WikiStore, WikiError
from .wiki_engine import snapshot, current, CONTENT_KINDS
from .chunking import load_chunk_policy
from .embedding_client import EmbeddingClient, CachedEmbedder, EmbeddingError
from .ai_settings import load_ai_settings


def _record_metric(slot,**values):
    from .task_queue import record_model_call
    try:record_model_call(slot,**values)
    except Exception as error:
        # Observability must not discard a completed remote result and trigger
        # an otherwise unnecessary repeat against the scarce model service.
        import logging
        logging.getLogger(__name__).warning('Retrieval model metrics unavailable (%s)',type(error).__name__)


class GatedEmbeddingClient:
    """Use the same endpoint capacity as answer and background LLM work.

    The gate covers cache misses only when passed into CachedEmbedder. Cache SQL
    writes happen after the gate/metric transaction has been released.
    """
    def __init__(self,config,database=None,priority=40):
        self.config=config;self.client=EmbeddingClient(config);self.database=database;self.priority=priority
    @property
    def requests(self):return getattr(self.client,'requests',0)
    def embed(self,texts):
        from .task_queue import model_slot,record_model_call
        if not texts:return []
        group=getattr(self.config.service,'resource_group','shared');status='failed';vectors=[];before=self.requests
        with model_slot(self.database,group,self.priority) as slot:
            started=time.monotonic()
            try:
                vectors=self.client.embed(texts);status='completed';return vectors
            finally:
                _record_metric(slot,role='embedding',identity=digest(self.config.identity()),resource_group=group,
                    elapsed_ms=(time.monotonic()-started)*1000,input_chars=sum(len(t) for t in texts),
                    output_chars=len(json.dumps(vectors)),usage={'requests':self.requests-before},status=status)


def refresh_local_index(db, paper_ids=None, *, wiki_ids=()):
    """Publish raw/claim FTS in the caller's transaction, without any model calls.

    A paper becomes available to the QA path independently of derived Wiki work.
    Only the requested papers/pages are replaced; unrelated indexes are retained.
    """
    from .pilot_api import claim_detail
    from .wiki_engine import _full_snapshot
    policy=load_chunk_policy(local_only=True)
    splitter=policy.splitter(policy.max_tokens-32)
    selected=set(paper_ids) if paper_ids is not None else {r[0] for r in db.execute('SELECT id FROM papers')}
    records=[]
    def add(kind,ref,expert,papers,title,text,metadata):
        for n,(offset,piece) in enumerate(splitter.chunk_indices(text)):
            meta=dict(metadata)
            if kind=='passage':meta['offset']+=offset
            values=(digest([kind,ref,n]),kind,ref,expert,json.dumps(sorted(papers)),title,piece,json.dumps(meta,ensure_ascii=False),digest([title,piece,meta,expert,sorted(papers)]))
            records.append(values)
    for pid in sorted(selected):
        paper=db.execute('SELECT id,title FROM papers WHERE id=?',(pid,)).fetchone()
        if not paper:continue
        for row in db.execute('SELECT * FROM claims WHERE paper_id=? ORDER BY id',(pid,)).fetchall():
            c=claim_detail(db,row)
            body=c['summary']+'\n'+c['scope']+'\n'+'\n'.join(m['label']+': '+m['raw_value']+'; '+m['kind']+'; '+m['sample']+'; '+ '; '.join(x['name']+': '+(x['raw_value'] or '미기재') for x in m['conditions']) for m in c['measurements'])
            add('claim',c['id'],None,[pid],paper['title'],body,{})
        spans=db.execute('''SELECT s.*,pg.text,d.id AS document_id FROM search_chunk_spans s
          JOIN pages pg ON pg.id=s.page_id JOIN paper_documents d ON d.current_run_id=pg.run_id
          WHERE d.paper_id=? AND pg.status='ready' ORDER BY s.chunk_id,s.ordinal''',(pid,)).fetchall()
        for span in spans:
            meta=dict(kind='source_text',paper_id=pid,page_id=span['page_id'],document_id=span['document_id'],page_number=span['page_number'],offset=span['start_offset'],chunk_id=span['chunk_id'])
            add('passage',span['chunk_id']+':'+str(span['ordinal']),None,[pid],paper['title'],span['text'][span['start_offset']:span['end_offset']],meta)
    wiki_ids=set(wiki_ids)
    if wiki_ids:
        database=next(r['file'] for r in db.execute('PRAGMA database_list') if r['name']=='main')
        store=WikiStore(database); state=_full_snapshot(connection=db)
        catalogue={r['id']:dict(r) for r in db.execute('SELECT * FROM wiki_files')}
        for wid in sorted(wiki_ids):
            row=catalogue.get(wid)
            if not row or row['kind'] not in CONTENT_KINDS or row['status'] in ('archived','needs_source','empty') or not current(row,state,catalogue):continue
            try:_,body=store.read(row)
            except WikiError:continue
            add('wiki',wid,row['expert_id'],json.loads(row['refs_json'])['paper_ids'],row['title'],body,{'revision':row['revision']})
    active={r[0] for r in records}
    for r in records:
        old=db.execute('SELECT input_hash,title,text,vector_json,model_fingerprint FROM retrieval_units WHERE id=?',(r[0],)).fetchone()
        if old and old['input_hash']==r[8]:continue
        # Metadata/revision edits do not invalidate embeddings of unchanged text.
        vector=(old['vector_json'],old['model_fingerprint']) if old and old['title']==r[5] and old['text']==r[6] else (None,None)
        db.execute('INSERT OR REPLACE INTO retrieval_units VALUES(?,?,?,?,?,?,?,?,?,?,?)',r+vector)
        db.execute('DELETE FROM retrieval_fts WHERE unit_id=?',(r[0],))
        db.execute('INSERT INTO retrieval_fts VALUES(?,?,?)',(r[0],r[5],r[6]))
    for row in db.execute('SELECT id,kind,reference_id,paper_ids_json FROM retrieval_units').fetchall():
        targeted=(row['kind'] in ('claim','passage') and bool(set(json.loads(row['paper_ids_json']))&selected)) or (row['kind']=='wiki' and row['reference_id'] in wiki_ids)
        if targeted and row['id'] not in active:
            db.execute('DELETE FROM retrieval_units WHERE id=?',(row['id'],))
            db.execute('DELETE FROM retrieval_fts WHERE unit_id=?',(row['id'],))
    from .knowledge_build import rebind_source_units
    rebind_source_units(db,selected)
    return {'units':len(records),'embedding_requests':0}


def reindex(database=None, heartbeat=lambda:None, *, scope='all', expert_id='', use_embeddings=True):
    if scope not in ('all','common','expert') or (scope=='expert') != bool(expert_id):
        raise WikiError('검색 색인 갱신 대상을 확인하세요.')
    clause,params=('1=1',()) if scope=='all' else (('expert_id IS NULL',()) if scope=='common' else ('expert_id=?',(expert_id,)))
    policy=load_chunk_policy() if use_embeddings else load_chunk_policy(local_only=True)
    splitter=policy.splitter(min(policy.max_tokens,policy.embedding.max_input_tokens if policy.embedding else policy.max_tokens)-32)
    store=WikiStore(database); state=snapshot(database)
    catalogue={r['id']:r for r in store.rows()}; records=[]
    def add(kind,ref,expert,papers,title,text,metadata):
        # Search caches are derived copies. Raw Markdown remains authoritative.
        for n,(offset,piece) in enumerate(splitter.chunk_indices(text)):
            key=digest([kind,ref,n])
            meta=dict(metadata)
            if kind=='passage':
                meta['offset']+=offset
            records.append(dict(id=key,kind=kind,reference_id=ref,expert_id=expert,paper_ids_json=json.dumps(sorted(papers)),
                title=title,text=piece,metadata_json=json.dumps(meta,ensure_ascii=False),input_hash=digest([title,piece,meta,expert,sorted(papers)])))
    for row in catalogue.values():
        if scope=='common' and row['scope']!='common' or scope=='expert' and row['expert_id']!=expert_id:
            continue
        if row['status'] not in ('archived','needs_source','empty') and row['kind'] in CONTENT_KINDS and current(row,state,catalogue):
            try:
                _,body=store.read(row)
                add('wiki',row['id'],row['expert_id'],json.loads(row['refs_json'])['paper_ids'],row['title'],body,{'revision':row['revision']})
            except WikiError:
                continue
    for p in (state['papers'].values() if scope!='expert' else ()):
        for c in p['claims']:
            body=c['summary']+'\n'+c['scope']+'\n'+'\n'.join(m['label']+': '+m['raw_value']+'; '+m['kind']+'; '+m['sample']+'; '+ '; '.join(x['name']+': '+(x['raw_value'] or '미기재') for x in m['conditions']) for m in c['measurements'])
            add('claim',c['id'],None,[p['id']],p['title'],body,{})
        # Exact source locations permit answering details omitted by the Wiki.
        # No LLM re-extraction is needed. Canonical claims still take precedence.
        with connect(database) as db:
            spans=[dict(r) for r in db.execute('SELECT s.* FROM search_chunk_spans s JOIN pages pg ON pg.id=s.page_id JOIN paper_documents d ON d.current_run_id=pg.run_id WHERE d.paper_id=? ORDER BY s.chunk_id,s.ordinal',(p['id'],))]
        pages={pg['id']:pg for pg in p['pages']}
        for span in spans:
            pg=pages.get(span['page_id'])
            if not pg or pg['status']!='ready':
                continue
            text=pg['text'][span['start_offset']:span['end_offset']]
            ref=span['chunk_id']+':'+str(span['ordinal'])
            metadata=dict(kind='source_text',paper_id=p['id'],page_id=pg['id'],document_id=pg['document_id'],page_number=pg['page_number'],offset=span['start_offset'],chunk_id=span['chunk_id'])
            add('passage',ref,None,[p['id']],p['title'],text,metadata)
    active={r['id'] for r in records}
    with connect(database) as db:
        for r in records:
            old=db.execute('SELECT input_hash FROM retrieval_units WHERE id=?',(r['id'],)).fetchone()
            if old and old[0]==r['input_hash']:
                continue
            db.execute('INSERT OR REPLACE INTO retrieval_units VALUES(?,?,?,?,?,?,?,?,?,?,?)',tuple(r.values())+(None,None))
            db.execute('DELETE FROM retrieval_fts WHERE unit_id=?',(r['id'],))
            db.execute('INSERT INTO retrieval_fts VALUES(?,?,?)',(r['id'],r['title'],r['text']))
        for row in db.execute('SELECT id FROM retrieval_units WHERE '+clause,params).fetchall():
            if row[0] not in active:
                db.execute('DELETE FROM retrieval_units WHERE id=?',(row[0],))
                db.execute('DELETE FROM retrieval_fts WHERE unit_id=?',(row[0],))
        db.commit()
    with connect(database) as db:
        if scope!='expert':
            from .knowledge_build import rebind_source_units
            rebind_source_units(db);db.commit()
    calls=0
    if policy.embedding:
        identity=digest(policy.embedding.identity()); client=GatedEmbeddingClient(policy.embedding,database)
        with connect(database) as db:
            pending=[dict(r) for r in db.execute('SELECT * FROM retrieval_units WHERE (model_fingerprint IS NULL OR model_fingerprint<>?) AND '+clause,(identity,)+params)]
        for row in pending:
            heartbeat()
            vector=client.embed([row['text']])[0]  # No DB write lock while awaiting API.
            with connect(database) as db:
                db.execute('UPDATE retrieval_units SET vector_json=?,model_fingerprint=? WHERE id=? AND input_hash=?',
                    (json.dumps(vector),identity,row['id'],row['input_hash'])); db.commit()
        calls=client.requests
    return {'units':len(records),'embedding_requests':calls}


def keywords(question, database=None, glossary=None):
    return query_rules(question,glossary,database)['keywords']


def retrieve(question,expert_ids,database=None,limit=30,glossary=None,*,local_only=False):
    expert_ids=list(dict.fromkeys(expert_ids))
    if not expert_ids:
        return [],['검색할 전문가 범위를 선택해 주세요.']
    terms=keywords(question,database,glossary); lexical=[]; semantic=[]; notices=[]
    marks=','.join('?' for _ in expert_ids)
    # Retrieval uses the selected experts' union. The answer layer separately
    # constrains each expert contribution to that expert's evidence IDs.
    scope=f'''(u.expert_id IS NULL OR (u.expert_id IN ({marks})
      AND u.expert_id NOT IN (SELECT expert_id FROM expert_deletions)
      AND u.expert_id NOT IN (SELECT expert_id FROM expert_policies WHERE enabled=0))) AND NOT EXISTS (
      SELECT 1 FROM json_each(u.paper_ids_json) p WHERE p.value NOT IN (
        SELECT ep.paper_id FROM expert_papers ep WHERE ep.expert_id IN ({marks})
        AND ep.expert_id NOT IN (SELECT expert_id FROM expert_deletions)
        AND ep.expert_id NOT IN (SELECT expert_id FROM expert_policies WHERE enabled=0)))
      AND (u.kind<>'wiki' OR EXISTS (SELECT 1 FROM wiki_files w WHERE w.id=u.reference_id
        AND w.status NOT IN ('archived','needs_source','empty')
        AND w.kind IN ('source','topic','specialization')))'''
    args=tuple(expert_ids)*2
    try:
        with connect(database) as db:
            if terms:
                expression=' OR '.join('"'+t.replace('"','""')+'"' for t in terms)
                lexical=[dict(r) for r in db.execute('SELECT u.*,bm25(retrieval_fts,0,2,1) AS score FROM retrieval_fts JOIN retrieval_units u ON u.id=retrieval_fts.unit_id WHERE retrieval_fts MATCH ? AND '+scope+' ORDER BY score LIMIT 60',(expression,*args))]
            else:
                notices.append('검색 가능한 키워드를 찾지 못했습니다.')
    except sqlite3.OperationalError as error:
        if 'no such table: retrieval_fts' not in str(error):
            raise
        notices.append('키워드 색인이 준비되지 않았습니다.')
    try:
        policy=load_chunk_policy() if not local_only else load_chunk_policy(local_only=True)
        if policy.embedding:
            pieces=policy.splitter(policy.embedding.max_input_tokens-32).chunks(question)
            if len(pieces)>1:
                notices.append('긴 질문으로 vector 검색을 생략했습니다. 질문을 짧게 나누면 더 정확하게 검색할 수 있습니다.')
            elif pieces:
                identity=digest(policy.embedding.identity())
                with connect(database) as db:
                    candidates=[dict(r) for r in db.execute('SELECT u.* FROM retrieval_units u WHERE '+scope+' AND model_fingerprint=?',(*args,identity))]
                    if candidates:
                        query=CachedEmbedder(db,policy.embedding,client=GatedEmbeddingClient(policy.embedding,database,priority=0)).embed([question])[0]; db.commit()
                        scored=[]
                        for row in candidates:
                            vector=json.loads(row['vector_json'])
                            if len(vector)!=len(query):
                                raise EmbeddingError('검색 vector 차원이 일치하지 않습니다.')
                            scored.append((sum(a*b for a,b in zip(query,vector)),row))
                        scored.sort(key=lambda r:-r[0]); semantic=[r for _,r in scored[:60]]
                    else:
                        notices.append('담당 범위에 해당 모델의 vector 색인이 없습니다. 키워드 검색 결과를 사용합니다.')
        else:
            notices.append('Embedding이 설정되지 않아 키워드 검색 결과를 사용합니다.')
    except (EmbeddingError,ValueError,OSError):
        notices.append('Embedding 연결·색인 확인이 필요하여 키워드 검색을 사용했습니다.')
    # Reciprocal rank fusion preserves both exact parameter matches and paraphrases.
    scores={}; merged={}
    for ranked in (lexical,semantic):
        for rank,row in enumerate(ranked):
            merged[row['id']]=row; scores[row['id']]=scores.get(row['id'],0)+1/(60+rank+1)
    result=sorted(merged.values(),key=lambda r:-scores[r['id']])[:limit]
    if not result:
        notices.append('담당 범위의 검색 색인에서 관련 근거를 찾지 못했습니다.')
    service=None if local_only else load_ai_settings().reranker
    if result and service and service.base_url and service.model and service.api_key.get_secret_value():
        try:
            result=rerank(question,result,service,database=database)
        except ValueError:
            notices.append('Reranker 연결·응답 형식을 확인해야 합니다. 결합 검색 순위를 사용했습니다.')
    return result,notices


def rerank(question,items,service,*,database=None):
    """Adapter: {model,query,documents} -> results[{index,relevance_score}].

    A list of {index,score} is accepted too. Confirm this adapter against the
    internal reranker contract before production; URL is used exactly as supplied.
    """
    import ssl
    from urllib.request import Request,build_opener,HTTPSHandler
    from urllib.parse import urlsplit
    from .embedding_client import NoRedirect
    from .task_queue import model_slot,record_model_call,WorkCancelled
    parsed=urlsplit(service.base_url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Invalid reranker endpoint')
    body=dict(model=service.model,query=question,documents=[i['text'] for i in items])
    try:
        req=Request(service.base_url,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+service.api_key.get_secret_value(),'Content-Type':'application/json'},method='POST')
        group=getattr(service,'resource_group','shared');status='failed';raw=b''
        with model_slot(database,group,0) as slot:
            started=time.monotonic()
            try:
                with build_opener(NoRedirect(),HTTPSHandler(context=ssl.create_default_context())).open(req,timeout=load_ai_settings().limits.reranker_timeout_seconds) as response:
                    raw=response.read(2_000_001)
                status='completed'
            finally:
                _record_metric(slot,role='reranker',identity=digest([service.base_url,service.model,getattr(service,'deployment_revision','')]),resource_group=group,
                    elapsed_ms=(time.monotonic()-started)*1000,input_chars=len(json.dumps(body,ensure_ascii=False)),output_chars=len(raw),usage={'requests':1},status=status)
        if len(raw)>2_000_000:
            raise ValueError()
        payload=json.loads(raw); ranks=payload.get('results') if isinstance(payload,dict) else payload
        if not isinstance(ranks,list) or len(ranks)!=len(items) or sorted(r.get('index') for r in ranks)!=list(range(len(items))):
            raise ValueError()
        values=[r.get('relevance_score',r.get('score')) for r in ranks]
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
            raise ValueError()
        return [items[r['index']] for r in sorted(ranks,key=lambda r:-r.get('relevance_score',r.get('score')))]
    except WorkCancelled:
        raise
    except Exception:
        raise ValueError('Reranker request failed or response contract differs') from None
