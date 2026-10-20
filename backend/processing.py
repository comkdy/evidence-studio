"""Local folder watcher and a small, durable user-facing processing ledger."""
import json
import sqlite3
import threading
import time
from datetime import datetime,timezone
from pathlib import Path
from fastapi import APIRouter
from .pilot_db import ROOT,DEFAULT_DB,connect,now,write_connection,is_database_busy
from .wiki_retry import failure_snapshot

SCAN_SECONDS=60
STABLE_SECONDS=5
router=APIRouter(prefix='/api/pilot',tags=['Processing'])


def record_expert_creation(db,expert_id,name,job_id):
    stamp=now()
    db.execute("""INSERT OR IGNORE INTO processing_tasks
      (kind,target,label,state,stage,wiki_job_id,created_at,updated_at)
      VALUES('expert',?,?,'db_ready','전문가 지식 연결 대기',?,?,?)""",(expert_id,name,job_id,stamp,stamp))


def update_task(database,task_id,**values):
    values['updated_at']=now()
    with write_connection(database) as db:
        db.execute('UPDATE processing_tasks SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),task_id))
        db.commit()


class RawScanner:
    def __init__(self,raw,database):
        self.raw=Path(raw);self.database=database

    def scan(self):
        stamp=now()
        try:
            if not self.raw.is_dir():
                raise FileNotFoundError()
            files=[p for p in self.raw.iterdir() if not p.is_symlink() and p.is_file() and p.suffix.lower()=='.pdf']
            with connect(self.database) as db:
                known={r[0] for r in db.execute('SELECT path FROM document_sources')}
                tasks={r['target']:dict(r) for r in db.execute("SELECT * FROM processing_tasks WHERE kind='paper'")}
                present=set()
                for path in sorted(files):
                    target=str(path.resolve());present.add(target)
                    old=tasks.get(target)
                    # Existing sources remain immutable; replacement needs explicit review.
                    if target in known:
                        continue
                    try:stat=path.stat()
                    except OSError:continue
                    identity=f'{stat.st_size}:{stat.st_mtime_ns}'
                    if old and old['state']=='running':continue
                    if not old or old['identity']!=identity:
                        settled=time.time()-stat.st_mtime>=STABLE_SECONDS
                        state='queued' if settled else 'stabilizing'
                        stage='논문 처리 대기' if settled else '파일 수신 중'
                        db.execute("""INSERT INTO processing_tasks(kind,target,label,identity,state,stage,created_at,updated_at)
                          VALUES('paper',?,?,?,?,?,?,?)
                          ON CONFLICT(kind,target) DO UPDATE SET identity=excluded.identity,state=excluded.state,
                          stage=excluded.stage,detail='',paper_id=NULL,wiki_job_id=NULL,needs_review=0,updated_at=excluded.updated_at""",
                          (target,path.name,identity,state,stage,stamp,stamp))
                    elif old['state']=='stabilizing' and time.time()-stat.st_mtime>=STABLE_SECONDS:
                        db.execute("UPDATE processing_tasks SET state='queued',stage='논문 처리 대기',updated_at=? WHERE id=?",(stamp,old['id']))
                for target,task in tasks.items():
                    if target not in present and task['state'] in ('stabilizing','queued'):
                        db.execute("UPDATE processing_tasks SET state='failed',stage='파일 확인 필요',detail='처리 전에 파일이 이동되거나 삭제되었습니다.',updated_at=? WHERE id=?",(stamp,task['id']))
                db.execute("INSERT INTO raw_watcher_status VALUES(1,?,?,'watching','') ON CONFLICT(id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at,checked_at=excluded.checked_at,state='watching',detail=''",(stamp,stamp))
                db.commit()
        except OSError:
            with connect(self.database) as db:
                db.execute("INSERT INTO raw_watcher_status VALUES(1,?,NULL,'error',?) ON CONFLICT(id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at,state='error',detail=excluded.detail",(stamp,'수집 폴더를 확인할 수 없습니다. 관리자에게 문의해 주세요.'))
                db.commit()


class TaskUpdateDeferred(Exception):
    """Keep only the ledger update pending; never repeat a completed ingest."""
    def __init__(self, task_id, values):
        self.task_id, self.values = task_id, values


def process_next(database):
    from .pilot_ingest import ingest_file
    try:
        with write_connection(database) as db:
            row=db.execute("SELECT * FROM processing_tasks WHERE kind='paper' AND state='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:return False
            task=dict(row)
            db.execute("UPDATE processing_tasks SET state='running',stage='논문 처리 시작',updated_at=? WHERE id=?",(now(),task['id']))
    except sqlite3.OperationalError as error:
        if not is_database_busy(error):raise
        return False

    def progress(stage):
        try:update_task(database,task['id'],stage=stage)
        except sqlite3.OperationalError as error:
            if not is_database_busy(error):raise

    retry=False
    try:
        result=ingest_file(task['target'],database,STABLE_SECONDS,progress=progress)
        state=result['status']
        if state=='deferred':
            values=dict(state='stabilizing',stage='파일 수신 중',detail='')
        elif state=='failed' or not result.get('paper_id'):
            values=dict(state='failed',stage='논문 확인 필요',detail=result['detail'])
        else:
            values=dict(state='db_ready',stage='Wiki 구성 대기',detail='',
                        paper_id=result['paper_id'],wiki_job_id=result.get('wiki_job_id'),needs_review=int(state in ('partial','needs_review')))
    except Exception as error:
        if is_database_busy(error):
            retry=True
            values=dict(state='queued',stage='DB 저장 대기',detail='DB가 사용 중입니다. 잠시 후 자동 재시도합니다.')
        else:
            values=dict(state='failed',stage='논문 처리 중단',detail='파일을 읽지 못했습니다. 파일 접근 권한과 PDF 형식을 확인해 주세요.')
    try:update_task(database,task['id'],**values)
    except sqlite3.OperationalError as error:
        if not is_database_busy(error):raise
        raise TaskUpdateDeferred(task['id'],values) from error
    return not retry



def start_ingestion_workers(stop,database):
    # This local SQLite app runs one server process. No separate scheduler needed.
    if Path(database).resolve()!=DEFAULT_DB.resolve():return ()
    with connect(database) as db:
        db.execute("UPDATE processing_tasks SET state='queued',stage='중단된 논문 처리 재개',updated_at=? WHERE kind='paper' AND state='running'",(now(),));db.commit()
    scanner=RawScanner(ROOT/'raw',database)
    def scan_loop():
        while not stop.is_set():
            try:scanner.scan()
            except Exception:
                import logging
                logging.getLogger(__name__).error('Folder monitor failed; will retry.')
            if stop.wait(SCAN_SECONDS):break
    def ingest_loop():
        pending=None
        while not stop.is_set():
            try:
                if pending:
                    update_task(database,pending.task_id,**pending.values)
                    pending=None
                elif process_next(database):continue
            except TaskUpdateDeferred as deferred:
                pending=deferred
            except Exception:
                import logging
                logging.getLogger(__name__).error('Document worker failed; will retry.')
            stop.wait(1)
    threads=(threading.Thread(target=scan_loop,name='raw-monitor',daemon=True),threading.Thread(target=ingest_loop,name='paper-ingest',daemon=True))
    for thread in threads:thread.start()
    return threads


def processing_status(database=None):
    with connect(database) as db:
        monitor=db.execute('SELECT * FROM raw_watcher_status WHERE id=1').fetchone()
        tasks=[dict(r) for r in db.execute('SELECT * FROM processing_tasks ORDER BY updated_at DESC')]
        jobs={r['id']:dict(r) for r in db.execute('SELECT * FROM wiki_jobs')}
        work=[dict(r) for r in db.execute("SELECT * FROM work_tasks WHERE kind LIKE 'knowledge.%' AND state<>'cancelled' ORDER BY updated_at DESC")]
        papers={r['id']:r['title'] for r in db.execute('SELECT id,title FROM papers')}
        experts={r['id']:r['name'] for r in db.execute('SELECT id,name FROM expert_categories')}
        membership={eid:{r[0] for r in db.execute('SELECT paper_id FROM expert_papers WHERE expert_id=?',(eid,))} for eid in experts}
        waiting=[dict(r) for r in db.execute("SELECT expert_id,refs_json FROM wiki_files WHERE status='awaiting_llm'")]
        # Unique Wiki jobs: one automatic job may belong to several paper cards.
        visible_jobs=sorted(jobs.values(),key=lambda j:({'running':0,'queued':1,'failed':2}.get(j['state'],3),-j['id']))[:12]
        from .wiki_usage import job_usages
        usage=job_usages(db,[j['id'] for j in visible_jobs])
        wiki_activity=[dict(id=j['id'],scope=j['scope'],state=j['state'],
            label=experts.get(j['expert_id'],'전문가')+' Wiki 갱신' if j['scope']=='expert' else '공통 Wiki 갱신' if j['scope']=='common' else '지식 자동 반영',
            created_at=j['created_at'],finished_at=j['finished_at'],error=j['error'],usage=usage[j['id']]) for j in visible_jobs]
        review_count=db.execute("SELECT count(*) FROM pages p JOIN paper_documents d ON d.current_run_id=p.run_id WHERE p.status NOT IN ('ready','excluded')").fetchone()[0]
    waiting_papers={pid for r in waiting if not r['expert_id'] for pid in json.loads(r['refs_json']).get('paper_ids',[])}
    waiting_experts={r['expert_id'] for r in waiting if r['expert_id']}
    for eid,pids in membership.items():
        if any(not r['expert_id'] and (sources:=set(json.loads(r['refs_json']).get('paper_ids',[]))) and sources.issubset(pids) for r in waiting):waiting_experts.add(eid)
    entries=[];linked=set()
    for task in tasks:
        job=jobs.get(task['wiki_job_id']);state=task['state'];stage=task['stage'];detail=task['detail']
        if job:linked.add(job['id'])
        if job and job['state']=='failed':
            retries=[j for j in jobs.values() if j['id']>job['id'] and
                     (j['scope']=='all' or task['kind']=='expert' and j['scope']=='expert' and j['expert_id']==task['target'])]
            if retries:
                job=max(retries,key=lambda j:j['id']);linked.add(job['id'])
        if state=='db_ready':
            if job and job['state'] in ('queued','running','failed'):
                state=job['state'];stage={'queued':'Wiki 구성 대기','running':'Wiki 구성 중','failed':'Wiki 구성 문제'}[state];detail=job['error'] if state in ('failed','queued') else ''
            else:
                migrated=bool(job and job['error'].startswith('수요 기반 지식 작업으로 전환'))
                state='completed';stage=('원문 검색 준비 완료 · 지식은 별도 구축' if task['kind']=='paper' else '전문가 범위 연결 완료 · 지식은 별도 구축') if not job or migrated else 'DB·Wiki 반영 완료'
                if migrated:detail=job['error']
                if job and not migrated and (task['paper_id'] in waiting_papers or task['kind']=='expert' and task['target'] in waiting_experts):
                    state='waiting';stage='LLM 종합 대기';detail='근거 연결은 완료했습니다. 사내 LLM 연결 후 종합 지식을 작성합니다.'
                if task['needs_review']:
                    state='review';stage='원문 검토 필요';detail='읽기 어려운 페이지가 있습니다. 자료 검토에서 확인해 주세요.'
        if state=='stabilizing':state='queued'
        entries.append(dict(id='task-'+str(task['id']),kind=task['kind'],label=papers.get(task['paper_id'],task['label']),state=state,stage=stage,detail=detail,
                            paper_id=task['paper_id'],expert_id=task['target'] if task['kind']=='expert' else None,
                            updated_at=max(task['updated_at'],job['finished_at'] or job['created_at']) if job else task['updated_at']))
    for job in jobs.values():
        if job['id'] in linked:continue
        label=experts.get(job['expert_id'],'전문가')+' Wiki 갱신' if job['scope']=='expert' else '공통 Wiki 갱신' if job['scope']=='common' else '지식 자동 반영'
        state='completed' if job['state']=='done' else job['state']
        migrated=job['state']=='done' and job['error'].startswith('수요 기반 지식 작업으로 전환')
        entries.append(dict(id='wiki-'+str(job['id']),kind='wiki',label=label,state=state,
                            stage='수요 기반 지식 작업으로 전환' if migrated else {'queued':'Wiki 반영 대기','running':'Wiki 반영 중','failed':'Wiki 반영 문제','completed':'변경사항 확인·반영 완료'}[state],
                            detail=job['error'] if migrated or state in ('failed','queued') else '',paper_id=None,expert_id=job['expert_id'] or None,updated_at=job['finished_at'] or job['created_at']))
    labels={'extract':'원문 근거 추출','review':'추출 근거 독립 검토','publish_source':'검토 근거 연결',
            'expert_index':'전문가 지식 연결','synthesize':'주제 지식 종합','review_synthesis':'종합 근거 독립 검토',
            'publish_synthesis':'검토된 종합 발행','embed':'검색 의미 색인'}
    for item in work:
        payload=json.loads(item['payload_json']);kind=item['kind'].split('.')[-1]
        pid=payload.get('paper_id');eid=payload.get('expert_id')
        state=item['state'];stage=labels.get(kind,'지식 갱신')
        entries.append(dict(id='work-'+item['id'],kind='knowledge',_work_key=(item['kind'],item['target']),label=papers.get(pid,experts.get(eid,'공통 지식')),
            state=state,stage=stage+{'queued':' 대기','running':' 중','completed':' 완료','failed':' 확인 필요'}.get(state,''),
            detail='원문 검색과 독립적인 백그라운드 작업입니다.' if state!='failed' else '처리를 완료하지 못했습니다. 설정과 작업 이력을 확인하세요.',
            paper_id=pid,expert_id=eid,updated_at=datetime.fromtimestamp(item['updated_at'],timezone.utc).isoformat()))
    entries.sort(key=lambda e:e['updated_at'],reverse=True)
    # Old failures superseded by successful retries remain in history, not alerts.
    seen=set()
    for e in entries:
        key=e.pop('_work_key',None) or (e['kind'],e['expert_id'],e['paper_id'] or e['label'])
        e['superseded']=key in seen;seen.add(key)
    counts={s:sum(e['state']==s and not e['superseded'] for e in entries) for s in ('queued','running','completed','failed','review','waiting')}
    alive=bool(monitor and (datetime.now(timezone.utc)-datetime.fromisoformat(monitor['heartbeat_at'])).total_seconds()<SCAN_SECONDS*3)
    watcher={'state':monitor['state'] if alive else 'stopped','checked_at':monitor['checked_at'] if monitor else None,'interval_seconds':SCAN_SECONDS,
             'detail':monitor['detail'] if monitor and alive else '폴더 감시 상태를 확인할 수 없습니다.'}
    active=[e for e in entries if not e['superseded'] and e['state'] in ('queued','running','failed','review','waiting')]
    active.sort(key=lambda e:{'running':0,'failed':1,'queued':2,'review':3,'waiting':4}[e['state']])
    recent=active[:8]+[e for e in entries if not e['superseded'] and e not in active][:max(0,12-len(active[:8]))]
    return {'watcher':watcher,'counts':counts,'review_pages':review_count,'tasks':recent,'total_tasks':len(entries),
            'wiki_jobs':wiki_activity,'wiki_retry':failure_snapshot(jobs.values()),
            'revision':max((e['updated_at'] for e in entries),default='')}


@router.get('/processing')
def activity():return processing_status()
