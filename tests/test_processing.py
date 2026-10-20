"""Folder ingestion and user-visible status, with an isolated corpus and Wiki."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from test_pilot import make_pdf, TEXT
from test_markdown_wiki import FakeLLM
from backend.pilot_db import initialize, connect, now
from backend.processing import RawScanner, process_next, processing_status, SCAN_SECONDS
from backend.wiki_engine import run_jobs, enqueue
from backend.expert_config import create_expert, ExpertCreate


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name);self.raw=self.root/'raw';self.raw.mkdir()
        self.db=self.root/'pilot.sqlite3'
        self.env=patch.dict(os.environ,{'EVIDENCE_DB_PATH':str(self.db),'AUTH_MODE':'local_admin',
            'LLM_BASE_URL':'','LLM_API_KEY':'','LLM_BASE_MODEL':'','EMBEDDING_BASE_URL':'',
            'EMBEDDING_API_KEY':'','EMBEDDING_MODEL':'','RERANKER_BASE_URL':''})
        self.env.start();initialize(self.db)
        self.scanner=RawScanner(self.raw,self.db)

    def tearDown(self):
        self.env.stop();self.tmp.cleanup()

    def pdf(self,name='paper.PDF',text=TEXT):
        path=self.raw/name;make_pdf(path,text)
        os.utime(path,(time.time()-10,time.time()-10))
        return path

    def task(self):
        with connect(self.db) as db:return dict(db.execute("SELECT * FROM processing_tasks WHERE kind='paper' ORDER BY id LIMIT 1").fetchone())

    def test_new_paper_is_ingested_once_and_links_real_wiki_job(self):
        self.pdf();self.scanner.scan()
        self.assertEqual(SCAN_SECONDS,60)
        self.assertEqual(self.task()['state'],'queued')
        self.assertTrue(process_next(self.db))
        task=self.task();self.assertEqual(task['state'],'db_ready');self.assertIsNone(task['wiki_job_id'])
        self.scanner.scan();self.assertFalse(process_next(self.db))
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM papers').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM processing_tasks').fetchone()[0],1)
            self.assertTrue(db.execute("SELECT 1 FROM work_tasks WHERE kind='knowledge.extract'").fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM wiki_jobs').fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        run_jobs(self.db,FakeLLM())
        status=processing_status(self.db)
        self.assertEqual(status['counts']['completed'],1)
        self.assertEqual(status['watcher']['state'],'watching')
        self.assertNotIn(str(self.root),json.dumps(status))

    def test_copying_file_waits_then_enters_queue(self):
        path=self.raw/'copying.pdf';make_pdf(path)
        self.scanner.scan();self.assertEqual(self.task()['state'],'stabilizing')
        self.assertFalse(process_next(self.db))
        os.utime(path,(time.time()-10,time.time()-10))
        self.scanner.scan();self.assertEqual(self.task()['state'],'queued')
        self.assertTrue(process_next(self.db))

    def test_corrupt_file_is_visible_and_repaired_content_is_retried(self):
        path=self.raw/'broken.pdf';path.write_bytes(b'broken PDF')
        os.utime(path,(time.time()-10,time.time()-10))
        self.scanner.scan();process_next(self.db)
        self.assertEqual(processing_status(self.db)['counts']['failed'],1)
        self.scanner.scan();self.assertFalse(process_next(self.db))
        self.pdf('broken.pdf');self.scanner.scan();self.assertTrue(process_next(self.db))
        self.assertEqual(self.task()['state'],'db_ready')

    def test_duplicate_content_reuses_paper_without_replacing_original(self):
        path=self.pdf();self.scanner.scan();process_next(self.db)
        duplicate=self.raw/'second.pdf';duplicate.write_bytes(path.read_bytes())
        os.utime(duplicate,(time.time()-10,time.time()-10))
        self.scanner.scan();process_next(self.db)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM papers').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM document_sources').fetchone()[0],2)
        self.pdf(path.name,TEXT+' Changed source.');self.scanner.scan()
        self.assertFalse(process_next(self.db))
        with connect(self.db) as db:self.assertNotIn('Changed source',db.execute('SELECT text FROM pages').fetchone()[0])

    def test_progress_is_readable_while_ingest_is_running(self):
        self.pdf();self.scanner.scan()
        def ingest(path,database,stable_seconds,progress):
            self.assertEqual(processing_status(database)['counts']['running'],1)
            progress('논문 내용 읽는 중')
            self.assertEqual(processing_status(database)['tasks'][0]['stage'],'논문 내용 읽는 중')
            return {'status':'failed','detail':'테스트 오류'}
        with patch('backend.pilot_ingest.ingest_file',side_effect=ingest):process_next(self.db)
        self.assertEqual(processing_status(self.db)['counts']['failed'],1)

    def test_busy_wiki_does_not_prevent_paper_ingestion_and_no_llm_is_not_complete(self):
        with connect(self.db) as db:
            db.execute('INSERT INTO wiki_worker_lease VALUES(1,?,?)',('busy-test-worker',time.time()+60));db.commit()
        self.pdf();self.scanner.scan();self.assertTrue(process_next(self.db))
        self.assertEqual(run_jobs(self.db)['status'],'idle')
        self.assertGreater(processing_status(self.db)['counts']['queued'],0)
        with connect(self.db) as db:db.execute('DELETE FROM wiki_worker_lease');db.commit()
        run_jobs(self.db)
        status=processing_status(self.db)
        paper=next(t for t in status['tasks'] if t['kind']=='paper')
        self.assertEqual(paper['state'],'completed');self.assertIn('원문 검색 준비 완료',paper['stage'])
        self.assertGreater(status['counts']['queued'],0)

    def test_expert_creation_is_tracked_idempotently_and_common_wiki_is_reused(self):
        self.pdf();self.scanner.scan();process_next(self.db);run_jobs(self.db,FakeLLM())
        draft=ExpertCreate(request_id=uuid4(),name='Retention',overview='DRAM retention',facets={'platform':['DRAM']})
        expert=create_expert(draft);create_expert(draft)
        with connect(self.db) as db:
            tasks=db.execute("SELECT * FROM processing_tasks WHERE kind='expert'").fetchall()
            self.assertEqual(len(tasks),1);self.assertEqual(tasks[0]['target'],expert['id'])
            self.assertIsNone(tasks[0]['wiki_job_id'])
            self.assertTrue(db.execute("SELECT 1 FROM work_tasks WHERE kind='knowledge.expert_index' AND target=?",(expert['id'],)).fetchone())
        self.assertGreater(processing_status(self.db)['counts']['queued'],0)
        llm=FakeLLM();run_jobs(self.db,llm);self.assertEqual(llm.calls,[])
        task=next(t for t in processing_status(self.db)['tasks'] if t['kind']=='expert')
        self.assertEqual(task['state'],'completed')

    def test_failed_wiki_status_clears_after_successful_retry(self):
        self.pdf();self.scanner.scan();process_next(self.db)
        with connect(self.db) as db:
            job=enqueue(db,'manual retry fixture')
            db.execute('UPDATE processing_tasks SET wiki_job_id=?',(job,))
            db.execute("UPDATE wiki_jobs SET state='failed',finished_at=?,error='Wiki 처리 실패'",(now(),));db.commit()
        self.assertEqual(processing_status(self.db)['counts']['failed'],1)
        with connect(self.db) as db:enqueue(db,'retry');db.commit()
        self.assertGreater(processing_status(self.db)['counts']['queued'],0)
        run_jobs(self.db,FakeLLM())
        self.assertEqual(processing_status(self.db)['counts']['failed'],0)
        self.assertEqual(processing_status(self.db)['counts']['completed'],1)

    def test_unreadable_and_removed_files_and_history_limit(self):
        path=self.pdf();self.scanner.scan();path.unlink();self.scanner.scan()
        self.assertEqual(processing_status(self.db)['counts']['failed'],1)
        RawScanner(self.root/'missing',self.db).scan()
        self.assertEqual(processing_status(self.db)['watcher']['state'],'error')
        with connect(self.db) as db:
            for n in range(30):
                db.execute("INSERT INTO wiki_jobs(reason,state,created_at,finished_at) VALUES(?,'done',?,?)",(str(n),now(),now()))
            db.commit()
        status=processing_status(self.db)
        self.assertLessEqual(len(status['tasks']),12)
        self.assertFalse(any(t['superseded'] for t in status['tasks']))
        self.assertEqual(status['counts']['completed'],1)

    def test_ocr_review_is_separate_from_wiki_completion(self):
        self.pdf(text='Image-only page');self.scanner.scan();process_next(self.db);run_jobs(self.db,FakeLLM())
        status=processing_status(self.db)
        self.assertEqual(status['review_pages'],1)
        self.assertEqual(status['counts']['review'],1)
        self.assertEqual(status['counts']['completed'],0)
