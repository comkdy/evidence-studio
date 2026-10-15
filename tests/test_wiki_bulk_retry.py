"""Bulk retry covers hidden failures and preserves completed evidence and Wiki."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from fastapi import HTTPException

import test_wiki_retry as fixtures
from test_markdown_wiki import FakeLLM
from backend.pilot_db import connect, now
from backend.pilot_ingest import ingest_file
from backend.processing import processing_status
from backend.wiki_api import retry_failed_wiki_jobs, WikiFailedJobsRetry
from backend.wiki_engine import run_jobs
from backend.wiki_store import WikiStore, WikiError

ENDPOINT = '/api/pilot/wiki-maintenance/jobs/retry-failed'


class WikiBulkRetryAPITests(unittest.TestCase):
    setUp = fixtures.WikiRetryAPITests.setUp
    tearDown = fixtures.WikiRetryAPITests.tearDown
    create = fixtures.WikiRetryAPITests.create
    sso = fixtures.WikiRetryAPITests.sso
    job = fixtures.WikiRetryAPITests.job

    def summary(self):
        return processing_status(self.db)['wiki_retry']

    def retry_all(self, snapshot=None):
        return self.client.post(ENDPOINT, json={'failure_snapshot': snapshot or self.summary()['snapshot']})

    def test_all_failures_including_hidden_jobs_only_are_queued(self):
        failed = [self.job()[0] for _ in range(16)]
        failed.append(self.job(scope='expert', expert_id='dram')[0])
        failed.append(self.job(scope='all', finished=False)[0])
        unchanged = [self.job(state=state)[0] for state in ('done', 'queued', 'running')]
        with connect(self.db) as db:
            before = {r['id']: dict(r) for r in db.execute('SELECT * FROM wiki_jobs')}
        status = processing_status(self.db)
        self.assertEqual(status['wiki_retry']['failed_count'], 18)
        self.assertEqual(len(status['wiki_jobs']), 12)
        with patch('backend.wiki_engine.reconcile', side_effect=AssertionError('No LLM in HTTP')):
            response = self.retry_all(status['wiki_retry']['snapshot'])
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()['queued_job_ids'], failed)
        self.assertEqual(response.json()['queued_count'], 18)
        with connect(self.db) as db:
            after = {r['id']: dict(r) for r in db.execute('SELECT * FROM wiki_jobs')}
        self.assertEqual(after.keys(), before.keys())
        for job in unchanged:
            self.assertEqual(after[job], before[job])
        for job in failed:
            self.assertEqual(after[job], dict(before[job], state='queued', finished_at=None, error=''))

    def test_unavailable_experts_are_reported_without_blocking_valid_jobs(self):
        valid, _ = self.job()
        invalid = [self.job(scope='expert', expert_id=e)[0] for e in ('missing', 'dram', 'nand')]
        with connect(self.db) as db:
            db.execute('INSERT INTO expert_deletions VALUES(?,?)', ('dram', now()))
            db.execute('INSERT OR REPLACE INTO expert_policies VALUES(?,?,?,?)', ('nand', '{}', 0, now()))
            db.commit()
        result = self.retry_all().json()
        self.assertEqual(result['queued_job_ids'], [valid])
        self.assertEqual(result['skipped'], [dict(job_id=i, reason='expert_unavailable') for i in invalid])
        with connect(self.db) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT id FROM wiki_jobs WHERE state='failed' ORDER BY id")], invalid)

    def test_stale_snapshot_does_not_retry_new_failures(self):
        first, _ = self.job()
        snapshot = self.summary()['snapshot']
        second, _ = self.job()
        self.assertEqual(self.retry_all(snapshot).status_code, 409)
        with connect(self.db) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT id FROM wiki_jobs WHERE state='failed' ORDER BY id")], [first, second])
        latest = self.summary()['snapshot']
        self.assertEqual(self.retry_all(latest).status_code, 202)
        self.assertEqual(self.retry_all(latest).status_code, 409)
        with connect(self.db) as db:
            db.execute("UPDATE wiki_jobs SET state='failed',finished_at=? WHERE id=?", (now(), first))
            db.commit()
        self.assertEqual(self.retry_all(snapshot).status_code, 409)

    def test_concurrent_requests_have_one_atomic_transition(self):
        for _ in range(5):
            self.job()
        request = WikiFailedJobsRetry(failure_snapshot=self.summary()['snapshot'])
        def attempt(_):
            try:
                return retry_failed_wiki_jobs(request)['queued_count']
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, range(4)))
        self.assertEqual(sorted(results), [5, 409, 409, 409])
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM wiki_jobs').fetchone()[0], 5)

    def test_admin_only_and_same_origin_required(self):
        self.job()
        body = {'failure_snapshot': self.summary()['snapshot']}
        self.create('3000000', role='superuser')
        for employee in ('3000000', '3000001'):
            with self.sso(employee):
                self.assertEqual(self.client.post(ENDPOINT, json=body, headers={'Origin': 'http://testserver'}).status_code, 403)
        with self.sso('9000001'):
            self.assertEqual(self.client.post(ENDPOINT, json=body).status_code, 403)
            self.assertEqual(self.client.post(ENDPOINT, json=body, headers={'Origin': 'http://testserver'}).status_code, 202)

    def test_empty_failure_set_is_a_noop(self):
        self.job(state='done')
        snapshot = self.summary()
        self.assertEqual(snapshot['failed_count'], 0)
        self.assertEqual(self.retry_all(snapshot['snapshot']).json(), dict(status='queued', queued_count=0, queued_job_ids=[], skipped=[]))

    def test_snapshot_ignores_nonfailed_jobs_but_tracks_new_failure_attempt(self):
        job, _ = self.job(finished=False)
        first = self.summary()
        self.job(state='done')
        self.assertEqual(self.summary(), first)
        with connect(self.db) as db:
            db.execute('UPDATE wiki_jobs SET finished_at=? WHERE id=?', (now(), job))
            db.commit()
        self.assertNotEqual(self.summary()['snapshot'], first['snapshot'])

    def test_missing_or_malformed_snapshot_is_rejected(self):
        self.job()
        for body in ({}, {'failure_snapshot': ''}, {'failure_snapshot': 'not-a-snapshot'}):
            self.assertEqual(self.client.post(ENDPOINT, json=body).status_code, 422)
        self.assertEqual(self.summary()['failed_count'], 1)


class WikiBulkRetryReuseTests(unittest.TestCase):
    setUp = fixtures.WikiRetryReuseTests.setUp
    tearDown = fixtures.WikiRetryReuseTests.tearDown
    pdf = fixtures.WikiRetryReuseTests.pdf

    def test_overlapping_failed_jobs_reuse_successful_paper_and_topic_without_reingest(self):
        ingest_file(self.pdf(), self.db, 0)
        with connect(self.db) as db:
            paper = db.execute('SELECT id FROM papers').fetchone()[0]
            db.execute('DELETE FROM paper_topics')
            db.executemany('INSERT INTO paper_topics(paper_id,tag,basis) VALUES(?,?,?)', [(paper, tag, 'test') for tag in ('Alpha', 'Beta')])
            db.commit()
        model = FakeLLM(); complete = model.complete_json
        def fail_beta(system, payload):
            if payload.get('title') == 'Beta':
                raise WikiError('Synthetic topic failure')
            return complete(system, payload)
        with patch.object(model, 'complete_json', side_effect=fail_beta):
            self.assertEqual(run_jobs(self.db, model)['status'], 'failed')
        with connect(self.db) as db:
            job = db.execute("SELECT * FROM wiki_jobs WHERE state='failed' LIMIT 1").fetchone()
            db.execute('INSERT INTO wiki_jobs(reason,created_at,state,finished_at,error,scope,expert_id) VALUES(?,?,?,?,?,?,?)',
                       tuple(job[k] for k in ('reason', 'created_at', 'state', 'finished_at', 'error', 'scope', 'expert_id')))
            before = {table: [tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in ('papers', 'extraction_runs', 'claims', 'measurements')}
            db.commit()
        store = WikiStore(self.db)
        successful = {r['id']: (r['revision'], (store.root/r['path']).read_bytes()) for r in store.rows()}
        request = WikiFailedJobsRetry(failure_snapshot=processing_status(self.db)['wiki_retry']['snapshot'])
        self.assertEqual(retry_failed_wiki_jobs(request)['queued_count'], 2)
        model.calls.clear()
        with patch('backend.pilot_ingest.ingest_file', side_effect=AssertionError('No PDF reingest')):
            run_jobs(self.db, model)
        self.assertEqual([p['title'] for p in model.calls], ['Beta'])
        for key, (revision, content) in successful.items():
            row = store.record(key)
            self.assertEqual(row['revision'], revision)
            self.assertEqual((store.root/row['path']).read_bytes(), content)
        with connect(self.db) as db:
            for table, expected in before.items():
                self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM '+table)], expected)
            self.assertEqual([r[0] for r in db.execute('SELECT state FROM wiki_jobs')], ['done', 'done'])

