import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from backend.pilot_db import connect
from backend import task_queue as queue


class DurableQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / 'work.sqlite3'
        with connect(self.database) as db:
            queue.ensure_schema(db)
            db.commit()
        self.seen = []
        queue.register_handler('test-step', lambda payload, context: self.seen.append(payload['name']) or {'done': payload['name']})

    def tearDown(self):
        self.temp.cleanup()

    def enqueue(self, name, priority=40, **kwargs):
        with connect(self.database) as db:
            task = queue.enqueue_task(db, 'test-step', name, 'v1', {'name': name}, priority=priority, **kwargs)
            db.commit()
        return task

    def test_shared_demand_priority_and_dependency_order(self):
        background = self.enqueue('background', 40)
        shared = self.enqueue('shared', 50)
        self.assertEqual(self.enqueue('shared', 40), shared)
        answer = self.enqueue('answer', 0, dependencies=[shared])
        for _ in range(3):
            self.assertTrue(queue.run_next(self.database))
        self.assertEqual(self.seen, ['shared', 'answer', 'background'])
        self.assertFalse(queue.run_next(self.database))
        self.assertEqual(queue.get_task(self.database, answer)['result'], {'done': 'answer'})
        self.assertEqual(self.enqueue('shared'), shared)
        self.assertFalse(queue.run_next(self.database))

    def test_retry_resumes_checkpoint_and_fences_cancelled_worker(self):
        calls = []
        def staged(payload, context):
            calls.append(context.checkpoint_data)
            if not context.checkpoint_data:
                context.checkpoint({'extracted': True})
                raise RuntimeError('secret provider body must not be logged')
            return {'reviewed': True}
        queue.register_handler('test-step', staged)
        job = self.enqueue('retry')
        queue.run_next(self.database)
        self.assertEqual(queue.get_task(self.database, job)['state'], 'queued')
        with connect(self.database) as db:
            db.execute('UPDATE work_tasks SET available_at=0 WHERE id=?', (job,)); db.commit()
        queue.run_next(self.database)
        self.assertEqual(calls, [{}, {'extracted': True}])
        self.assertEqual(queue.get_task(self.database, job)['state'], 'completed')

        def cancel_inside(payload, context):
            with connect(self.database) as db:
                queue.cancel_task(db, context.job_id); db.commit()
            context.checkpoint({'must_not_publish': True})
        queue.register_handler('test-step', cancel_inside)
        job = self.enqueue('cancel')
        queue.run_next(self.database)
        self.assertEqual(queue.get_task(self.database, job)['state'], 'cancelled')
        self.assertEqual(queue.get_task(self.database, job)['result'], {})

    def test_expired_lease_and_failed_dependency_terminate(self):
        parent = self.enqueue('parent', max_attempts=1)
        child = self.enqueue('child', dependencies=[parent])
        with connect(self.database) as db:
            db.execute("UPDATE work_tasks SET state='running',attempts=1,lease_owner='dead',lease_until=0 WHERE id=?", (parent,))
            db.commit()
        self.assertFalse(queue.run_next(self.database))
        self.assertEqual(queue.get_task(self.database, child)['state'], 'failed')
        with connect(self.database) as db:
            revived = queue.enqueue_task(db,'test-step','parent','v1',revive=True)
            db.commit()
        self.assertEqual(revived, parent)
        self.assertEqual(queue.get_task(self.database, parent)['state'], 'queued')

    def test_dependency_cycles_rejected(self):
        a = self.enqueue('a')
        b = self.enqueue('b', dependencies=[a])
        with connect(self.database) as db:
            with self.assertRaises(ValueError):
                queue.enqueue_task(db, 'test-step', 'a', 'v1', dependencies=[b])

    def test_two_dispatchers_cannot_run_same_task(self):
        self.enqueue('only')
        barrier = threading.Barrier(3)
        results = []
        def worker():
            barrier.wait(); results.append(queue.run_next(self.database))
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)
        self.assertCountEqual(results, [True, False])
        self.assertEqual(self.seen, ['only'])

    def test_model_gate_yields_next_slot_to_foreground(self):
        order, threads = [], []
        with queue.model_slot(self.database, 'same-server', 40):
            for name, priority in [('background', 40), ('answer', 0)]:
                def call(name=name, priority=priority):
                    with queue.model_slot(self.database, 'same-server', priority):
                        order.append(name)
                thread = threading.Thread(target=call)
                thread.start(); threads.append(thread)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with connect(self.database) as db:
                    count = db.execute("SELECT count(*) FROM model_waiters WHERE state='waiting'").fetchone()[0]
                if count == 2:
                    break
                time.sleep(.01)
            self.assertEqual(count, 2)
        for thread in threads:
            thread.join(5)
        self.assertEqual(order, ['answer', 'background'])

    def test_expired_waiting_ticket_is_recreated_without_losing_fifo(self):
        acquired, errors = threading.Event(), []
        def wait_for_slot():
            try:
                with queue.model_slot(self.database, 'lease-recovery', 40):
                    acquired.set()
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=wait_for_slot, daemon=True)
        with queue.model_slot(self.database, 'lease-recovery', 40):
            thread.start()
            deadline = time.monotonic() + 3
            ticket = None
            while time.monotonic() < deadline:
                with connect(self.database) as db:
                    ticket = db.execute("SELECT * FROM model_waiters WHERE state='waiting'").fetchone()
                if ticket:
                    break
                time.sleep(.01)
            self.assertIsNotNone(ticket)
            with connect(self.database) as db:
                db.execute('UPDATE model_waiters SET lease_until=0 WHERE id=?', (ticket['id'],))
                db.commit()
            deadline = time.monotonic() + 3
            recovered = None
            while time.monotonic() < deadline:
                with connect(self.database) as db:
                    recovered = db.execute('SELECT * FROM model_waiters WHERE id=? AND lease_until>0', (ticket['id'],)).fetchone()
                if recovered:
                    break
                time.sleep(.01)
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered['created_at'], ticket['created_at'])
            self.assertFalse(acquired.is_set())
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(acquired.is_set())
        self.assertEqual(errors, [])

    def test_model_metrics_failure_preserves_result_and_original_error(self):
        from backend.ai_settings import ModelSettings
        from backend.llm_client import CompanyLLM, LLMError
        from pydantic import SecretStr
        import sqlite3
        model = CompanyLLM(ModelSettings('http://model.invalid/v1', 'fixture', SecretStr('fixture')), database=self.database)
        with patch.object(model, '_request_json', return_value={'answer':'kept'}), \
             patch('backend.task_queue.record_model_call', side_effect=sqlite3.OperationalError('secret diagnostic')):
            with self.assertLogs('backend.llm_client', level='WARNING') as logged:
                self.assertEqual(model.complete_json('fixture', {}), {'answer':'kept'})
            self.assertNotIn('secret diagnostic', ' '.join(logged.output))
        with patch.object(model, '_request_json', side_effect=LLMError('original provider failure')), \
             patch('backend.task_queue.record_model_call', side_effect=sqlite3.OperationalError('secret diagnostic')):
            with self.assertLogs('backend.llm_client', level='WARNING'):
                with self.assertRaisesRegex(LLMError, 'original provider failure'):
                    model.complete_json('fixture', {})
        with connect(self.database) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM model_waiters').fetchone()[0], 0)

    def test_running_dependency_promotion_updates_its_waiting_model_priority(self):
        job = self.enqueue('running-background', 50)
        with connect(self.database) as db:
            db.execute("UPDATE work_tasks SET state='running',lease_owner='fixture',lease_until=? WHERE id=?",
                       (time.time()+120,job))
            db.commit()
        context = queue.WorkContext(self.database, queue.get_task(self.database,job), 'fixture')
        order, errors, threads = [], [], []
        def call(name, work=None):
            token = queue._context.set(work)
            try:
                with queue.model_slot(self.database, 'promotion', 10):
                    order.append(name)
            except Exception as error:
                errors.append(error)
            finally:
                queue._context.reset(token)
        with queue.model_slot(self.database, 'promotion', 0):
            for name,work in [('promoted',context),('other',None)]:
                thread = threading.Thread(target=call,args=(name,work),daemon=True)
                thread.start(); threads.append(thread)
            deadline = time.monotonic()+3
            while time.monotonic()<deadline:
                with connect(self.database) as db:
                    count = db.execute("SELECT count(*) FROM model_waiters WHERE state='waiting'").fetchone()[0]
                if count==2:break
                time.sleep(.01)
            self.assertEqual(count,2)
            with connect(self.database) as db:
                queue.enqueue_task(db,'test-step','running-background','v1',priority=0)
                db.commit()
            deadline = time.monotonic()+3
            priority = None
            while time.monotonic()<deadline:
                with connect(self.database) as db:
                    row = db.execute('SELECT priority FROM model_waiters WHERE job_id=?',(job,)).fetchone()
                priority = row[0] if row else None
                if priority==0:break
                time.sleep(.01)
            self.assertEqual(priority,0)
        for thread in threads:thread.join(3)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors,[])
        self.assertEqual(order,['promoted','other'])


if __name__ == '__main__':
    unittest.main()
