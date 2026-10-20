"""Durable, resumable work and a priority gate shared by all model roles.

SQLite transactions only protect claims/checkpoints; handlers and HTTP calls run
outside them. Leases fence stale workers. A running HTTP request is never claimed
to be preemptible: foreground work takes the next available model slot.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
from pathlib import Path
import threading
import time
import uuid

from .pilot_db import connect, db_path

USER_ANSWER = 0
ANSWER_EVIDENCE = 10
ACTIVE_REFRESH = 20
PLANNED_RESEARCH = 30
EXPERT_BUILD = 40
EXPLORATION = 50
LINT = 60
LEASE_SECONDS = 120
_handlers = {}
_foreground_dispatchers = set()
_context = ContextVar('knowledge_work_context', default=None)


class WorkCancelled(RuntimeError):
    """Cancellation or lost ownership; the worker must not publish results."""


TaskCancelled = WorkCancelled


def current_context():
    return _context.get()


def ensure_schema(db):
    # execute (not executescript): callers may already be in an ingest transaction.
    statements = [
        """CREATE TABLE IF NOT EXISTS work_tasks(
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL,
        input_hash TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
        result_json TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'queued',
        priority INTEGER NOT NULL DEFAULT 50, attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 2, available_at REAL NOT NULL,
        lease_owner TEXT, lease_until REAL, created_at REAL NOT NULL,
        updated_at REAL NOT NULL, error TEXT, UNIQUE(kind,target,input_hash))""",
        """CREATE TABLE IF NOT EXISTS work_dependencies(
        task_id TEXT NOT NULL REFERENCES work_tasks(id),
        depends_on TEXT NOT NULL REFERENCES work_tasks(id),
        PRIMARY KEY(task_id,depends_on), CHECK(task_id<>depends_on))""",
        "CREATE INDEX IF NOT EXISTS work_ready ON work_tasks(state,priority,available_at)",
        """CREATE TABLE IF NOT EXISTS model_waiters(
        id TEXT PRIMARY KEY, resource_group TEXT NOT NULL, priority INTEGER NOT NULL,
        state TEXT NOT NULL, lease_until REAL NOT NULL, created_at REAL NOT NULL,
        job_id TEXT)""",
        "CREATE INDEX IF NOT EXISTS model_waiting ON model_waiters(resource_group,state,priority,created_at)",
        """CREATE TABLE IF NOT EXISTS model_call_metrics(
        id TEXT PRIMARY KEY, job_id TEXT, role TEXT NOT NULL, model_identity TEXT NOT NULL,
        resource_group TEXT NOT NULL, queued_ms REAL NOT NULL, elapsed_ms REAL NOT NULL,
        input_chars INTEGER NOT NULL, output_chars INTEGER NOT NULL,
        usage_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL, created_at REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS work_stage_metrics(
        task_id TEXT NOT NULL, attempt INTEGER NOT NULL, kind TEXT NOT NULL,
        queued_ms REAL NOT NULL, elapsed_ms REAL NOT NULL, status TEXT NOT NULL,
        created_at REAL NOT NULL, PRIMARY KEY(task_id,attempt))""",
    ]
    for statement in statements:
        db.execute(statement)


def enqueue_task(db, kind, target, input_hash, payload=None, priority=50,
                 dependencies=(), max_attempts=2, revive=False):
    ensure_schema(db)
    stamp = time.time()
    existing = db.execute('SELECT id FROM work_tasks WHERE kind=? AND target=? AND input_hash=?',
                          (kind, target, input_hash)).fetchone()
    task_id = existing['id'] if existing else uuid.uuid4().hex
    if not existing:
        db.execute('''INSERT OR IGNORE INTO work_tasks(id,kind,target,input_hash,payload_json,priority,
                      max_attempts,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                   (task_id, kind, target, input_hash, json.dumps(payload or {}, ensure_ascii=False),
                    int(priority), max(1, int(max_attempts)), stamp, stamp, stamp))
        task_id = db.execute('SELECT id FROM work_tasks WHERE kind=? AND target=? AND input_hash=?',
                             (kind, target, input_hash)).fetchone()['id']
        db.execute('UPDATE work_tasks SET priority=MIN(priority,?) WHERE id=?', (priority, task_id))
    else:
        db.execute('UPDATE work_tasks SET priority=MIN(priority,?),updated_at=? WHERE id=?',
                   (priority, stamp, task_id))
        if revive:
            db.execute("""UPDATE work_tasks SET state='queued',attempts=0,error=NULL,
                lease_owner=NULL,lease_until=NULL,available_at=?,updated_at=?
                WHERE id=? AND state IN ('failed','cancelled')""", (stamp, stamp, task_id))
    for dependency in dependencies:
        if dependency == task_id:
            raise ValueError('A task cannot depend on itself')
        # Reject cycles, including those created by later additions to shared work.
        cycle = db.execute('''WITH RECURSIVE parents(id) AS (
            SELECT depends_on FROM work_dependencies WHERE task_id=? UNION
            SELECT d.depends_on FROM work_dependencies d JOIN parents p ON d.task_id=p.id)
            SELECT 1 FROM parents WHERE id=?''', (dependency, task_id)).fetchone()
        if cycle:
            raise ValueError('Work dependency cycle')
        db.execute('INSERT OR IGNORE INTO work_dependencies VALUES(?,?)', (task_id, dependency))
    # An answer waiting for shared background evidence raises its dependencies too.
    db.execute('''WITH RECURSIVE parents(id) AS (
        SELECT depends_on FROM work_dependencies WHERE task_id=? UNION
        SELECT d.depends_on FROM work_dependencies d JOIN parents p ON d.task_id=p.id)
        UPDATE work_tasks SET priority=MIN(priority,?) WHERE id IN (SELECT id FROM parents)''',
               (task_id, priority))
    return task_id


def register_handler(kind, handler):
    _handlers[kind] = handler


def _decoded(row):
    if row is None:
        return None
    result = dict(row)
    result['status'] = result['state']
    result['payload'] = json.loads(result['payload_json'])
    result['result'] = json.loads(result['result_json'])
    return result


def get_task(database, task_id):
    with connect(database) as db:
        return _decoded(db.execute('SELECT * FROM work_tasks WHERE id=?', (task_id,)).fetchone())


def cancel_task(db, task_id):
    # A request owns only its own task, never the shared evidence dependencies.
    return bool(db.execute("""UPDATE work_tasks SET state='cancelled',lease_owner=NULL,
        lease_until=NULL,updated_at=? WHERE id=? AND state IN ('queued','running')""",
                           (time.time(), task_id)).rowcount)


class WorkContext:
    def __init__(self, database, task, owner):
        self.database = Path(database)
        self.job_id = task['id']
        self.priority = task['priority']
        self.owner = owner
        self.checkpoint_data = json.loads(task['result_json'])

    def check_cancelled(self):
        with connect(self.database) as db:
            row = db.execute('SELECT state,lease_owner,lease_until,priority FROM work_tasks WHERE id=?',
                             (self.job_id,)).fetchone()
        if not row or row['state'] != 'running' or row['lease_owner'] != self.owner or (row['lease_until'] or 0) < time.time():
            raise WorkCancelled('작업이 취소되었거나 실행 권한이 만료되었습니다.')
        # A foreground request can raise a shared prerequisite after it started.
        self.priority = row['priority']

    def checkpoint(self, data):
        self.check_cancelled()
        with connect(self.database) as db:
            changed = db.execute('''UPDATE work_tasks SET result_json=?,updated_at=?
                WHERE id=? AND state='running' AND lease_owner=? AND lease_until>=?''',
                (json.dumps(data, ensure_ascii=False), time.time(), self.job_id, self.owner, time.time())).rowcount
            db.commit()
        if not changed:
            raise WorkCancelled('작업 실행 권한이 만료되었습니다.')
        self.checkpoint_data = data


def _renew_work(database, task_id, owner):
    with connect(database) as db:
        db.execute("UPDATE work_tasks SET lease_until=? WHERE id=? AND state='running' AND lease_owner=?",
                   (time.time() + LEASE_SECONDS, task_id, owner))
        db.commit()


@contextmanager
def _heartbeat(callback):
    stopped = threading.Event()
    def beat():
        while not stopped.wait(10):
            try:
                callback()
            except Exception:
                # The lease itself decides ownership after a transient DB failure.
                pass
    thread = threading.Thread(target=beat, daemon=True, name='work-lease')
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1)


def run_next(database=None, min_priority=None, max_priority=None, kinds=None):
    database = Path(database or db_path())
    kinds = tuple(kinds if kinds is not None else _handlers)
    if not kinds:
        return False
    stamp = time.time()
    owner = uuid.uuid4().hex
    with connect(database) as db:
        ensure_schema(db)
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        db.execute("""UPDATE work_tasks SET state=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'queued' END,
            lease_owner=NULL,lease_until=NULL,updated_at=?,error='작업 중단 후 실행 권한 만료'
            WHERE state='running' AND lease_until<?""", (stamp, stamp))
        # Cascade failed prerequisites so requests do not stay queued forever.
        while db.execute("""UPDATE work_tasks SET state='failed',error='선행 작업 실패 또는 취소',updated_at=?
            WHERE state='queued' AND id IN (SELECT d.task_id FROM work_dependencies d
            JOIN work_tasks p ON p.id=d.depends_on WHERE p.state IN ('failed','cancelled'))""", (stamp,)).rowcount:
            pass
        clauses = ["state='queued'", 'available_at<=?', f"kind IN ({','.join('?' for _ in kinds)})",
                   "NOT EXISTS(SELECT 1 FROM work_dependencies d JOIN work_tasks p ON p.id=d.depends_on WHERE d.task_id=work_tasks.id AND p.state<>'completed')"]
        args = [stamp, *kinds]
        if min_priority is not None:
            clauses.append('priority>=?'); args.append(min_priority)
        if max_priority is not None:
            clauses.append('priority<=?'); args.append(max_priority)
        row = db.execute('SELECT * FROM work_tasks WHERE ' + ' AND '.join(clauses) + ' ORDER BY priority,created_at,id LIMIT 1', args).fetchone()
        if row is None:
            db.commit()
            return False
        task = dict(row)
        db.execute("UPDATE work_tasks SET state='running',attempts=attempts+1,lease_owner=?,lease_until=?,updated_at=? WHERE id=?",
                   (owner, stamp + LEASE_SECONDS, stamp, task['id']))
        db.commit()
    context = WorkContext(database, task, owner)
    token = _context.set(context)
    started = time.monotonic()
    state, error, result = 'completed', None, None
    try:
        with _heartbeat(lambda: _renew_work(database, task['id'], owner)):
            result = _handlers[task['kind']](json.loads(task['payload_json']), context)
            context.check_cancelled()
    except WorkCancelled:
        state, error = 'cancelled', '작업 취소 또는 실행 권한 만료'
    except Exception as exc:
        state = 'failed' if task['attempts'] + 1 >= task['max_attempts'] else 'queued'
        # Never persist provider response bodies, keys, or user prompt text in logs.
        error = f'작업 단계 실패 ({type(exc).__name__})'
    finally:
        _context.reset(token)
    elapsed = (time.monotonic() - started) * 1000
    with connect(database) as db:
        fields = "state=?,error=?,lease_owner=NULL,lease_until=NULL,available_at=?,updated_at=?"
        args = [state, error, time.time() + min(300, 2 ** (task['attempts'] + 1)), time.time()]
        if state == 'completed':
            fields += ',result_json=?'; args.append(json.dumps(result if result is not None else context.checkpoint_data, ensure_ascii=False))
        args += [task['id'], owner, time.time()]
        db.execute('UPDATE work_tasks SET ' + fields + " WHERE id=? AND state='running' AND lease_owner=? AND lease_until>=?", args)
        db.execute('INSERT OR REPLACE INTO work_stage_metrics VALUES(?,?,?,?,?,?,?)',
                   (task['id'], task['attempts'] + 1, task['kind'], max(0, stamp-task['created_at'])*1000,
                    elapsed, state, time.time()))
        db.commit()
    return True


_fallback_condition = threading.Condition()
_fallback_waiters = {}
_fallback_active = set()


class ModelQueueTimeout(TimeoutError):
    pass


@contextmanager
def model_slot(database=None, resource_group='shared', priority=0, *, deadline=None):
    """One in-flight call per resource group, priority FIFO at call boundaries."""
    context = current_context()
    database = Path(database or (context.database if context else db_path()))
    priority = context.priority if context else priority
    ticket, started = uuid.uuid4().hex, time.monotonic()
    created_at = time.time()
    def check_wait():
        from .research_progress import checkpoint
        checkpoint()
        if context:
            context.check_cancelled()
        if deadline is not None and time.monotonic() >= deadline:
            raise ModelQueueTimeout('Model capacity wait exceeded its deadline')
    if not database.is_file():
        # Standalone contract tests/tools must not create the operational database.
        with _fallback_condition:
            _fallback_waiters[ticket] = (resource_group, priority, time.monotonic())
            try:
                while resource_group in _fallback_active or min(
                    (v[1], v[2], k) for k, v in _fallback_waiters.items() if v[0] == resource_group)[2] != ticket:
                    check_wait()
                    _fallback_condition.wait(.1)
                check_wait()
                _fallback_active.add(resource_group)
            finally:
                _fallback_waiters.pop(ticket,None)
                _fallback_condition.notify_all()
        try:
            yield {'queued_ms': (time.monotonic()-started)*1000, 'database': None, 'job_id': None}
        finally:
            with _fallback_condition:
                _fallback_active.discard(resource_group)
                _fallback_condition.notify_all()
        return
    with connect(database) as db:
        ensure_schema(db)
        db.execute('INSERT INTO model_waiters VALUES(?,?,?,?,?,?,?)',
                   (ticket, resource_group, priority, 'waiting', time.time()+LEASE_SECONDS, created_at, context.job_id if context else None))
        db.commit()
    acquired = False
    try:
        while not acquired:
            check_wait()
            if context:
                context.check_cancelled()
                priority = context.priority
            with connect(database) as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM model_waiters WHERE lease_until<?', (time.time(),))
                # A paused process may lose its waiting lease. No HTTP call has
                # started yet, so recreate that ticket at its original FIFO
                # position instead of waiting forever for a deleted row.
                db.execute('INSERT OR IGNORE INTO model_waiters VALUES(?,?,?,?,?,?,?)',
                    (ticket, resource_group, priority, 'waiting', time.time()+LEASE_SECONDS,
                     created_at, context.job_id if context else None))
                db.execute('UPDATE model_waiters SET lease_until=?,priority=? WHERE id=?', (time.time()+LEASE_SECONDS, priority, ticket))
                first = db.execute("SELECT id FROM model_waiters WHERE resource_group=? AND state='waiting' ORDER BY priority,created_at,id LIMIT 1", (resource_group,)).fetchone()
                running = db.execute("SELECT 1 FROM model_waiters WHERE resource_group=? AND state='running' LIMIT 1", (resource_group,)).fetchone()
                # Foreground dispatcher may still be assembling evidence and
                # has not entered this gate yet. Yield before sending another
                # background call. Only the separate foreground dispatcher is
                # considered here; waiting on queued work in this same dispatcher
                # would deadlock it.
                foreground = priority >= 30 and str(database.resolve()) in _foreground_dispatchers and db.execute("""SELECT 1 FROM work_tasks t
                    WHERE priority<30 AND id<>? AND ((state='running' AND lease_until>?) OR
                    (state='queued' AND available_at<=? AND NOT EXISTS(
                    SELECT 1 FROM work_dependencies d JOIN work_tasks p ON p.id=d.depends_on
                    WHERE d.task_id=t.id AND p.state<>'completed'))) LIMIT 1""",
                    (context.job_id if context else '', time.time(), time.time())).fetchone()
                if first and first['id'] == ticket and not running and not foreground:
                    db.execute("UPDATE model_waiters SET state='running' WHERE id=?", (ticket,))
                    acquired = True
                db.commit()
            if not acquired:
                time.sleep(.05)
        def renew():
            with connect(database) as db:
                db.execute('UPDATE model_waiters SET lease_until=? WHERE id=?', (time.time()+LEASE_SECONDS, ticket))
                db.commit()
        with _heartbeat(renew):
            check_wait()
            yield {'queued_ms': (time.monotonic()-started)*1000, 'database': database,
                   'job_id': context.job_id if context else None}
        if context:
            context.check_cancelled()
    finally:
        with connect(database) as db:
            db.execute('DELETE FROM model_waiters WHERE id=?', (ticket,))
            db.commit()


def record_model_call(slot, *, role, identity, resource_group, elapsed_ms, input_chars,
                      output_chars, usage, status):
    if not slot.get('database'):
        return
    with connect(slot['database']) as db:
        db.execute('INSERT INTO model_call_metrics VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (uuid.uuid4().hex, slot['job_id'], role, identity, resource_group,
                    slot['queued_ms'], elapsed_ms, input_chars, output_chars,
                    json.dumps(usage), status, time.time()))
        db.commit()


def start_workers(stop, database=None):
    """One background dispatcher plus foreground dispatcher; one shared model slot.

    Registration imports are intentionally late to avoid ingest/router cycles.
    """
    database = Path(database or db_path())
    from . import knowledge_build, research_requests, knowledge_learning, wiki_lint
    for module in (knowledge_build, research_requests, knowledge_learning, wiki_lint):
        module.register_handlers()
    def dispatch(foreground):
        if foreground:
            _foreground_dispatchers.add(str(database.resolve()))
        try:
            while not stop.is_set():
                try:
                    ran = run_next(database, max_priority=29) if foreground else run_next(database, min_priority=30)
                except Exception as error:
                    logging.getLogger(__name__).warning('Work dispatcher unavailable (%s)', type(error).__name__)
                    ran = False
                if not ran:
                    stop.wait(.25 if foreground else 1)
        finally:
            if foreground:
                _foreground_dispatchers.discard(str(database.resolve()))
    def maintain():
        while not stop.is_set():
            try:
                with connect(database) as db:
                    knowledge_build.enqueue_background_work(db)
                    knowledge_learning.enqueue_daily_work(db)
                    wiki_lint.enqueue_scheduled_work(db)
                    db.commit()
            except Exception as error:
                logging.getLogger(__name__).warning('Knowledge scheduling unavailable (%s)', type(error).__name__)
            stop.wait(60)
    threads = tuple(threading.Thread(target=fn, daemon=True, name=name) for fn, name in (
        (lambda: dispatch(True), 'answer-worker'), (lambda: dispatch(False), 'knowledge-worker'),
        (maintain, 'knowledge-maintenance')))
    for thread in threads:
        thread.start()
    return threads
