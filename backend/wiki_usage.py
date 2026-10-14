"""Durable Wiki job usage. No prompts, responses, URLs, or credentials are recorded."""
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from uuid import uuid4

from .pilot_db import connect, now, is_database_busy
from .wiki_store import digest


_trace = ContextVar('wiki_usage_trace', default=None)
_call = ContextVar('wiki_usage_call', default=None)
PHASES = frozenset(('source','source_part','topic','topic_part','specialization'))


def elapsed(start, end):
    return max(0, round((datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds()*1000))


def recover_runs(db):
    """A new lease owner closes orphaned attempts at their last observed heartbeat."""
    for row in db.execute("SELECT * FROM wiki_job_runs WHERE state='running'").fetchall():
        cutoff=row['heartbeat_at']
        for call in db.execute("SELECT id,sent_at FROM wiki_model_calls WHERE run_id=? AND state IN ('waiting','running')",(row['id'],)).fetchall():
            db.execute("UPDATE wiki_model_calls SET state='interrupted',finished_at=?,elapsed_ms=? WHERE id=?",
                (cutoff,elapsed(call['sent_at'],cutoff) if call['sent_at'] else 0,call['id']))
        db.execute("UPDATE wiki_job_runs SET state='interrupted',finished_at=?,elapsed_ms=? WHERE id=?",
            (cutoff,elapsed(row['started_at'],cutoff),row['id']))


class WikiJobTrace:
    def __init__(self, database, db, job_id, owner):
        self.database=database
        self.id=uuid4().hex
        self.started=time.monotonic()
        self.calls={}
        self.dirty=set()
        stamp=now()
        db.execute("INSERT INTO wiki_job_runs(id,job_id,owner,state,started_at,heartbeat_at) VALUES(?,?,?,'running',?,?)",
                   (self.id,job_id,owner,stamp,stamp))

    @contextmanager
    def capture(self):
        token=_trace.set(self)
        try:yield self
        finally:_trace.reset(token)

    def write_calls(self, db, ids):
        for key in ids:
            row=self.calls[key]
            columns=list(row)
            db.execute('INSERT INTO wiki_model_calls('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+') '
                'ON CONFLICT(id) DO UPDATE SET '+','.join(k+'=excluded.'+k for k in columns if k!='id'),tuple(row.values()))

    def changed(self, row):
        self.calls[row['id']]=row
        self.dirty.add(row['id'])
        # A metrics write must never hold a transaction across network I/O, or
        # turn transient DB contention into a repeated/broken model invocation.
        try:
            with connect(self.database,timeout=.05) as db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute("SELECT 1 FROM wiki_job_runs WHERE id=? AND state='running'",(self.id,)).fetchone():
                    return
                self.write_calls(db,self.dirty)
                db.execute('UPDATE wiki_job_runs SET heartbeat_at=? WHERE id=?',(now(),self.id))
                db.commit()
            self.dirty.clear()
        except sqlite3.OperationalError as error:
            if not is_database_busy(error):raise
            # Pending snapshots are flushed on the next event or final job commit.

    def finish(self, db, state):
        if not db.execute("SELECT 1 FROM wiki_job_runs WHERE id=? AND state='running'",(self.id,)).fetchone():
            return
        # Write all snapshots, not just dirty entries: the caller's commit can fail
        # and the same finalization must remain safely repeatable after rollback.
        self.write_calls(db,self.calls)
        stamp=now()
        db.execute('UPDATE wiki_job_runs SET state=?,finished_at=?,heartbeat_at=?,elapsed_ms=? WHERE id=?',
            (state,stamp,stamp,max(0,round((time.monotonic()-self.started)*1000)),self.id))


class ModelCall:
    def __init__(self, trace, client, payload):
        self.trace=trace
        self.sent_clock=None
        phase=payload.get('page_kind','other') if isinstance(payload,dict) else 'other'
        self.row=dict(id=uuid4().hex,run_id=trace.id,model_key=digest(client.identity),
            model=client.service.model[:256],role='light' if getattr(client,'wiki_role','default')=='light' else 'default',
            phase=phase if phase in PHASES else 'other',state='waiting',created_at=now(),sent_at=None,
            finished_at=None,elapsed_ms=0,input_tokens=None,output_tokens=None,total_tokens=None)
        trace.changed(self.row)
        self.token=_call.set(self)

    def sent(self):
        self.sent_clock=time.monotonic()
        self.row.update(state='running',sent_at=now())
        self.trace.changed(self.row)

    def finish(self, status, usage):
        try:
            usage=usage if isinstance(usage,dict) else {}
            def count(key):
                value=usage.get(key)
                return value if type(value) is int and 0<=value<=2**63-1 else None
            incoming,outgoing,total=count('prompt_tokens'),count('completion_tokens'),count('total_tokens')
            if total is None and incoming is not None and outgoing is not None:
                total=incoming+outgoing if incoming+outgoing<=2**63-1 else None
            self.row.update(state='completed' if status=='completed' else 'failed',finished_at=now(),
                elapsed_ms=max(0,round((time.monotonic()-self.sent_clock)*1000)) if self.sent_clock is not None else 0,
                input_tokens=incoming,output_tokens=outgoing,total_tokens=total)
            self.trace.changed(self.row)
        finally:_call.reset(self.token)


def begin_model_call(client,payload):
    trace=_trace.get()
    return ModelCall(trace,client,payload) if trace else None


def mark_model_sent():
    call=_call.get()
    if call:call.sent()


def summarize(calls, stamp):
    sent=[c for c in calls if c['sent_at']]
    result=dict(calls=len(sent),completed=sum(c['state']=='completed' for c in sent),
        failed=sum(c['state']=='failed' for c in sent),running=sum(c['state']=='running' for c in sent),
        interrupted=sum(c['state']=='interrupted' for c in sent),
        waiting=sum(c['state']=='waiting' for c in calls),not_sent=sum(not c['sent_at'] and c['state'] not in ('waiting',) for c in calls),
        model_elapsed_ms=sum(elapsed(c['sent_at'],stamp) if c['state']=='running' else c['elapsed_ms'] for c in sent))
    for key in ('input','output','total'):
        known=[c[key+'_tokens'] for c in sent if c[key+'_tokens'] is not None]
        result[key+'_tokens']=sum(known) if known else (None if sent else 0)
        result[key+'_reports']=len(known)
    return result


def job_usages(db, job_ids):
    """One bounded query for the visible jobs; old jobs remain explicitly unknown."""
    if not job_ids:return {}
    marks=','.join('?' for _ in job_ids)
    runs=[dict(r) for r in db.execute(f'SELECT * FROM wiki_job_runs WHERE job_id IN ({marks}) ORDER BY started_at,id',job_ids)]
    calls=[dict(r) for r in db.execute(f'''SELECT c.*,r.job_id FROM wiki_model_calls c
        JOIN wiki_job_runs r ON r.id=c.run_id WHERE r.job_id IN ({marks}) ORDER BY c.created_at,c.id''',job_ids)]
    stamp=now();result={}
    for job_id in job_ids:
        own_runs=[r for r in runs if r['job_id']==job_id]
        if not own_runs:
            result[job_id]=dict(available=False,models=[])
            continue
        own_calls=[c for c in calls if c['job_id']==job_id]
        models=[]
        for key in dict.fromkeys((c['model_key'],c['role']) for c in own_calls):
            rows=[c for c in own_calls if (c['model_key'],c['role'])==key]
            models.append(dict(key=key[0]+':'+key[1],model=rows[0]['model'],role=key[1],
                phases=list(dict.fromkeys(c['phase'] for c in rows)),**summarize(rows,stamp)))
        result[job_id]=dict(available=True,attempts=len(own_runs),
            interrupted_attempts=sum(r['state']=='interrupted' for r in own_runs),
            started_at=own_runs[0]['started_at'],
            elapsed_ms=sum(elapsed(r['started_at'],stamp) if r['state']=='running' else r['elapsed_ms'] for r in own_runs),
            models=models,**summarize(own_calls,stamp))
    return result
