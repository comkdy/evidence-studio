"""Retry only a failed job; preserve successful work and enforce admin access."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_auth as auth_fixtures
import test_processing as processing_fixtures
from test_markdown_wiki import FakeLLM
from test_pilot import TEXT
from backend.pilot_db import connect, now
from backend.pilot_ingest import ingest_file
from backend.wiki_api import retry_wiki_job, WikiJobRetry
from backend.wiki_engine import enqueue, run_jobs
from backend.wiki_store import WikiStore, WikiError
from backend.processing import processing_status


class WikiRetryAPITests(unittest.TestCase):
    setUp = auth_fixtures.IdentityTests.setUp
    tearDown = auth_fixtures.IdentityTests.tearDown
    create = auth_fixtures.IdentityTests.create
    sso = auth_fixtures.IdentityTests.sso

    def job(self, state='failed', scope='common', expert_id='', finished=True):
        stamp = now() if finished else None
        with connect(self.db) as db:
            job_id = db.execute('INSERT INTO wiki_jobs(reason,created_at,state,finished_at,error,scope,expert_id) VALUES(?,?,?,?,?,?,?)',
                ('original request', now(), state, stamp, 'Original failure', scope, expert_id)).lastrowid
            db.commit()
        return job_id, stamp

    def retry(self, job_id, stamp):
        return self.client.post(f'/api/pilot/wiki-maintenance/jobs/{job_id}/retry', json={'failed_at': stamp})

    def test_only_selected_failed_job_requeues_with_same_identity_and_scope(self):
        selected, stamp = self.job(scope='expert', expert_id='dram')
        other, _ = self.job()
        done, _ = self.job(state='done')
        with patch('backend.wiki_engine.reconcile', side_effect=AssertionError('HTTP must only enqueue')):
            response = self.retry(selected, stamp)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json(), dict(status='queued', job_id=selected, already_requested=False))
        with connect(self.db) as db:
            rows = {r['id']: dict(r) for r in db.execute('SELECT * FROM wiki_jobs')}
        self.assertEqual(len(rows), 3)
        self.assertEqual((rows[selected]['scope'], rows[selected]['expert_id']), ('expert', 'dram'))
        self.assertEqual(rows[selected]['reason'], 'original request')
        self.assertEqual(rows[selected]['state'], 'queued')
        self.assertEqual(rows[other]['state'], 'failed')
        self.assertEqual(rows[done]['state'], 'done')

    def test_duplicate_requests_do_not_create_another_job(self):
        job, stamp = self.job()
        self.retry(job, stamp)
        again = self.retry(job, stamp)
        self.assertTrue(again.json()['already_requested'])
        with connect(self.db) as db:
            db.execute("UPDATE wiki_jobs SET state='running' WHERE id=?", (job,)); db.commit()
        running = self.retry(job, stamp)
        self.assertEqual(running.json()['status'], 'running')
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM wiki_jobs').fetchone()[0], 1)

    def test_concurrent_clicks_have_one_state_transition(self):
        job, stamp = self.job()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: retry_wiki_job(job, WikiJobRetry(failed_at=stamp)), range(4)))
        self.assertEqual(sum(not r['already_requested'] for r in results), 1)

    def test_completed_unknown_and_stale_failures_are_rejected(self):
        done, stamp = self.job(state='done')
        self.assertEqual(self.retry(done, stamp).status_code, 409)
        self.assertEqual(self.retry(999999, stamp).status_code, 404)
        failed, stamp = self.job()
        self.assertEqual(self.retry(failed, 'older failure').status_code, 409)
        self.assertEqual(self.client.post(f'/api/pilot/wiki-maintenance/jobs/{failed}/retry', json={}).status_code, 422)
        self.retry(failed, stamp)
        with connect(self.db) as db:
            db.execute("UPDATE wiki_jobs SET state='failed',finished_at=? WHERE id=?", (now(), failed)); db.commit()
        self.assertEqual(self.retry(failed, stamp).status_code, 409)

    def test_admin_only_and_cookie_origin_protection(self):
        job, stamp = self.job()
        self.create('3000000', role='superuser')
        for employee in ('3000000', '3000001'):
            with self.sso(employee):
                response = self.client.post(f'/api/pilot/wiki-maintenance/jobs/{job}/retry',
                    json={'failed_at': stamp}, headers={'Origin': 'http://testserver'})
                self.assertEqual(response.status_code, 403)
        with self.sso('9000001'):
            self.assertEqual(self.retry(job, stamp).status_code, 403)
            response = self.client.post(f'/api/pilot/wiki-maintenance/jobs/{job}/retry',
                json={'failed_at': stamp}, headers={'Origin': 'http://testserver'})
            self.assertEqual(response.status_code, 202)

    def test_missing_expert_is_not_requeued(self):
        job, stamp = self.job(scope='expert', expert_id='deleted-expert')
        self.assertEqual(self.retry(job, stamp).status_code, 409)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT state FROM wiki_jobs WHERE id=?', (job,)).fetchone()[0], 'failed')

    def test_legacy_failure_without_finish_timestamp_can_retry(self):
        job, stamp = self.job(finished=False)
        self.assertEqual(self.retry(job, stamp).status_code, 202)

    def test_old_failed_job_remains_visible_ahead_of_recent_successes(self):
        job, _ = self.job()
        for _ in range(15):
            self.job(state='done')
        visible = processing_status(self.db)['wiki_jobs']
        self.assertEqual(visible[0]['id'], job)
        self.assertEqual(visible[0]['state'], 'failed')


class WikiRetryReuseTests(unittest.TestCase):
    setUp = processing_fixtures.ProcessingTests.setUp
    tearDown = processing_fixtures.ProcessingTests.tearDown
    pdf = processing_fixtures.ProcessingTests.pdf

    def retry_failed(self):
        with connect(self.db) as db:
            job = db.execute("SELECT * FROM wiki_jobs WHERE state='failed' ORDER BY id LIMIT 1").fetchone()
        self.assertIsNotNone(job)
        retry_wiki_job(job['id'], WikiJobRetry(failed_at=job['finished_at']))
        return job['id']

    def test_successful_paper_is_not_reingested_or_sent_to_llm_on_job_retry(self):
        for i in range(2):
            ingest_file(self.pdf(f'paper-{i}.pdf', TEXT+f' Additional result number {i}.'), self.db, 0)
        with connect(self.db) as db:
            enqueue(db, 'manual retry fixture')
            db.commit()
        model = FakeLLM(); complete = model.complete_json; attempted = []
        def fail_second(system, payload):
            attempted.append(payload)
            if len(attempted) == 2:
                raise WikiError('quote_not_found: synthetic failure')
            return complete(system, payload)
        with patch.object(model, 'complete_json', side_effect=fail_second):
            self.assertEqual(run_jobs(self.db, model)['status'], 'failed')
        store = WikiStore(self.db)
        completed = {r['id']: r for r in store.rows()}
        self.assertEqual(len(completed), 1)
        contents = {r['path']: (store.root/r['path']).read_bytes() for r in completed.values()}
        with connect(self.db) as db:
            before_runs = db.execute('SELECT count(*) FROM extraction_runs').fetchone()[0]
            before_claims = db.execute('SELECT count(*) FROM claims').fetchone()[0]
        job_id = self.retry_failed(); model.calls.clear()
        with patch('backend.pilot_ingest.ingest_file', side_effect=AssertionError('No PDF ingest during Wiki retry')):
            run_jobs(self.db, model)
        self.assertFalse(any(p['evidence'] == attempted[0]['evidence'] for p in model.calls))
        self.assertEqual(sum(p['page_kind'] == 'source' for p in model.calls), 1)
        for row in completed.values():
            self.assertEqual(store.record(row['id'])['revision'], row['revision'])
            self.assertEqual((store.root/row['path']).read_bytes(), contents[row['path']])
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM extraction_runs').fetchone()[0], before_runs)
            self.assertEqual(db.execute('SELECT count(*) FROM claims').fetchone()[0], before_claims)
            self.assertEqual(db.execute('SELECT state FROM wiki_jobs WHERE id=?', (job_id,)).fetchone()[0], 'done')
            self.assertEqual([r[0] for r in db.execute('SELECT state FROM wiki_job_runs WHERE job_id=? ORDER BY started_at', (job_id,))], ['failed', 'done'])
        visible = next(j for j in processing_status(self.db)['wiki_jobs'] if j['id'] == job_id)
        self.assertEqual(visible['usage']['attempts'], 2)

    def test_successful_common_wiki_is_not_resynthesized_on_retry(self):
        ingest_file(self.pdf(), self.db, 0)
        with connect(self.db) as db:
            paper = db.execute('SELECT id FROM papers').fetchone()[0]
            db.execute('DELETE FROM paper_topics')
            db.executemany('INSERT INTO paper_topics(paper_id,tag,basis) VALUES(?,?,?)', [(paper, tag, 'test') for tag in ('Alpha', 'Beta')])
            enqueue(db, 'manual retry fixture')
            db.commit()
        model = FakeLLM(); complete = model.complete_json
        def fail_beta(system, payload):
            if payload.get('title') == 'Beta':
                raise WikiError('Synthetic topic failure')
            return complete(system, payload)
        with patch.object(model, 'complete_json', side_effect=fail_beta):
            self.assertEqual(run_jobs(self.db, model)['status'], 'failed')
        store = WikiStore(self.db)
        rows = {r['id']: r['content_hash'] for r in store.rows()}
        self.retry_failed(); model.calls.clear(); run_jobs(self.db, model)
        self.assertEqual([p['title'] for p in model.calls], ['Beta'])
        self.assertTrue(all(store.record(key)['content_hash'] == value for key, value in rows.items()))
