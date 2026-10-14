"""Usage is durable, scoped to Wiki jobs, and never invents provider token counts."""
import io
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from pydantic import SecretStr
from backend.ai_settings import ModelSettings
from backend.llm_client import CompanyLLM, LLMCapacityError
from backend.pilot_db import connect, initialize, now, write_connection
from backend.processing import processing_status, process_next
from backend.wiki_engine import enqueue, run_jobs
from backend.wiki_usage import WikiJobTrace, job_usages
import test_processing as fixtures
import test_markdown_wiki as wiki_fixtures


class WikiUsageTests(unittest.TestCase):
    setUp=fixtures.ProcessingTests.setUp
    tearDown=fixtures.ProcessingTests.tearDown
    pdf=fixtures.ProcessingTests.pdf

    def client(self,name='main',light=False):
        client=CompanyLLM(ModelSettings('https://private-model.example/v1',name,SecretStr('private-test-key')))
        if light:client.wiki_role='light'
        return client

    def response(self,usage=None,content=None):
        envelope={'choices':[{'finish_reason':'stop','message':{'content':content or '{"sections":[]}'}}]}
        if usage is not None:envelope['usage']=usage
        return io.BytesIO(json.dumps(envelope).encode())

    def run_job(self,action,scope='common'):
        with connect(self.db) as db:
            job_id=enqueue(db,scope=scope);db.commit()
        with patch('backend.wiki_engine.reconcile',side_effect=lambda *a,**k:action() or {}):
            result=run_jobs(self.db)
        return job_id,result

    def usage(self,job_id):
        with connect(self.db) as db:return job_usages(db,[job_id])[job_id]

    def test_models_usage_and_elapsed_are_persisted_and_visible_during_http(self):
        main,light=self.client(),self.client('small',True)
        seen=[]
        def http(request,**kwargs):
            usage=processing_status(self.db)['wiki_jobs'][0]['usage']
            seen.append(usage)
            self.assertEqual(usage['running'],1)
            self.assertEqual(usage['calls'],len(seen))
            # No SQLite writer transaction is held while the provider is running.
            with write_connection(self.db) as db:db.execute('SELECT 1')
            return self.response({'prompt_tokens':100,'completion_tokens':20,'total_tokens':120})
        with patch('backend.llm_client.build_opener') as opener:
            opener.return_value.open.side_effect=http
            def action():
                light.complete_json('private instructions',{'page_kind':'source','secret-source':'private document'})
                main.complete_json('private instructions',{'page_kind':'topic'})
                main.complete_json('private instructions',{'page_kind':'specialization'})
            job_id,_=self.run_job(action)
        u=self.usage(job_id)
        self.assertEqual((u['calls'],u['completed'],u['input_tokens'],u['output_tokens'],u['total_tokens']),(3,3,300,60,360))
        self.assertEqual(u['total_reports'],3)
        self.assertEqual({(m['model'],m['role'],m['calls']) for m in u['models']},{('small','light',1),('main','default',2)})
        self.assertEqual(u['models'][1]['phases'],['topic','specialization'])
        self.assertGreaterEqual(u['elapsed_ms'],u['model_elapsed_ms'])
        self.assertIsNone(seen[0]['total_tokens'])
        serialized=json.dumps(processing_status(self.db))
        for secret in ('private-model.example','private-test-key','private instructions','private document'):
            self.assertNotIn(secret,serialized)
        initialize(self.db)
        self.assertEqual(self.usage(job_id),u)  # restart/schema initialization does not erase records

    def test_missing_partial_and_zero_usage_are_not_confused(self):
        usages=[None,{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0},
                {'prompt_tokens':30},{'prompt_tokens':10,'completion_tokens':5},
                {'prompt_tokens':True,'completion_tokens':-1}]
        with patch('backend.llm_client.build_opener') as opener:
            opener.return_value.open.side_effect=[self.response(u) for u in usages]
            client=self.client()
            job_id,_=self.run_job(lambda:[client.complete_json('system',{}) for _ in usages])
        u=self.usage(job_id)
        self.assertEqual(u['calls'],5)
        self.assertEqual((u['input_tokens'],u['input_reports']),(40,3))
        self.assertEqual((u['output_tokens'],u['output_reports']),(5,2))
        self.assertEqual((u['total_tokens'],u['total_reports']),(15,2))

    def test_failed_json_still_records_reported_tokens_and_failed_call(self):
        with patch('backend.llm_client.build_opener') as opener:
            opener.return_value.open.return_value=self.response({'prompt_tokens':50,'completion_tokens':7},'{invalid')
            job_id,result=self.run_job(lambda:self.client().complete_json('system',{}))
        self.assertEqual(result['status'],'failed')
        u=self.usage(job_id)
        self.assertEqual((u['calls'],u['failed'],u['total_tokens']),(1,1,57))

    def test_timeout_records_one_failed_attempt_with_unknown_tokens(self):
        with patch('backend.llm_client.build_opener') as opener:
            opener.return_value.open.side_effect=TimeoutError()
            job_id,result=self.run_job(lambda:self.client().complete_json('system',{}))
            self.assertEqual(opener.return_value.open.call_count,1)
        u=self.usage(job_id)
        self.assertEqual(result['status'],'failed')
        self.assertEqual((u['calls'],u['failed']),(1,1))
        self.assertIsNone(u['total_tokens'])
        self.assertEqual(u['total_reports'],0)

    def test_queue_failure_is_not_counted_as_an_http_call(self):
        with patch('backend.llm_client._model_gate.slot',side_effect=LLMCapacityError('queue full')), \
             patch('backend.llm_client.build_opener') as opener:
            job_id,_=self.run_job(lambda:self.client().complete_json('system',{}))
            opener.assert_not_called()
        u=self.usage(job_id)
        self.assertEqual((u['calls'],u['not_sent'],u['total_tokens']),(0,1,0))

    def test_client_history_and_other_thread_calls_are_not_recounted(self):
        client=self.client()
        with patch('backend.llm_client.build_opener') as opener:
            opener.return_value.open.side_effect=lambda *a,**k:self.response({'prompt_tokens':10,'completion_tokens':5})
            client.complete_json('outside Wiki job',{})
            first,_=self.run_job(lambda:client.complete_json('inside Wiki job',{}))
            def second():
                with ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(self.client().complete_json,'unrelated question',{}).result()
            second_id,_=self.run_job(second)
        self.assertEqual(self.usage(first)['calls'],1)
        self.assertEqual(self.usage(second_id)['calls'],0)
        self.assertEqual(self.usage(second_id)['total_tokens'],0)

    def test_busy_metrics_writes_are_flushed_without_repeating_provider_call(self):
        with patch('backend.wiki_usage.connect',side_effect=sqlite3.OperationalError('database is locked')), \
             patch('backend.llm_client.build_opener') as opener:
            opener.return_value.open.side_effect=lambda *a,**k:self.response({'prompt_tokens':5,'completion_tokens':6})
            job_id,_=self.run_job(lambda:self.client().complete_json('system',{}))
            self.assertEqual(opener.return_value.open.call_count,1)
        self.assertEqual(self.usage(job_id)['total_tokens'],11)

    def test_finalization_rollback_keeps_completed_call_on_deferred_job(self):
        original=WikiJobTrace.finish
        attempts=[]
        def finish(trace,db,state):
            original(trace,db,state)
            attempts.append(state)
            if len(attempts)==1:raise sqlite3.OperationalError('database is locked')
        with patch.object(WikiJobTrace,'finish',finish),patch('backend.llm_client.build_opener') as opener:
            opener.return_value.open.return_value=self.response({'prompt_tokens':3,'completion_tokens':4})
            job_id,result=self.run_job(lambda:self.client().complete_json('system',{}))
            self.assertEqual(opener.return_value.open.call_count,1)
        self.assertEqual(result['status'],'deferred')
        u=self.usage(job_id)
        self.assertEqual((u['calls'],u['total_tokens'],u['interrupted_attempts']),(1,7,1))

    def test_orphan_recovery_retains_unknown_call_and_excludes_downtime(self):
        start=datetime.now(timezone.utc)-timedelta(hours=1)
        with write_connection(self.db) as db:
            job_id=enqueue(db,scope='common')
            db.execute("UPDATE wiki_jobs SET state='running' WHERE id=?",(job_id,))
            db.execute("INSERT INTO wiki_job_runs(id,job_id,owner,state,started_at,heartbeat_at) VALUES('old',?,'expired','running',?,?)",
                       (job_id,start.isoformat(),(start+timedelta(seconds=30)).isoformat()))
            db.execute("INSERT INTO wiki_model_calls(id,run_id,model_key,model,role,phase,state,created_at,sent_at) VALUES('call','old','k','main','default','source','running',?,?)",
                       (start.isoformat(),start.isoformat()))
        with patch('backend.wiki_engine.reconcile',return_value={}):run_jobs(self.db)
        u=self.usage(job_id)
        self.assertEqual((u['attempts'],u['interrupted_attempts'],u['calls'],u['interrupted']),(2,1,1,1))
        self.assertGreaterEqual(u['elapsed_ms'],30000)
        self.assertLess(u['elapsed_ms'],40000)
        self.assertIsNone(u['total_tokens'])

    def test_legacy_unknown_and_new_no_call_job_are_distinct(self):
        with write_connection(self.db) as db:
            old=db.execute("INSERT INTO wiki_jobs(reason,state,created_at,finished_at) VALUES('legacy','done',?,?)",(now(),now())).lastrowid
        recent,_=self.run_job(lambda:None)
        self.assertFalse(self.usage(old)['available'])
        self.assertTrue(self.usage(recent)['available'])
        self.assertEqual(self.usage(recent)['calls'],0)
        self.assertNotIn('elapsed_ms',self.usage(old))

    def test_linked_tasks_share_one_usage_record_and_history_is_bounded(self):
        job_id,_=self.run_job(lambda:None)
        with write_connection(self.db) as db:
            for eid in ('dram','nand'):
                db.execute("INSERT INTO processing_tasks(kind,target,label,state,stage,wiki_job_id,created_at,updated_at) VALUES('expert',?,?,'db_ready','ready',?,?,?)",
                           (eid,eid,job_id,now(),now()))
        status=processing_status(self.db)
        self.assertEqual(len(status['tasks']),2)
        self.assertEqual([j['id'] for j in status['wiki_jobs']],[job_id])
        with write_connection(self.db) as db:
            for i in range(20):db.execute("INSERT INTO wiki_jobs(reason,state,created_at) VALUES(?,'done',?)",(str(i),now()))
        status=processing_status(self.db)
        self.assertEqual(len(status['wiki_jobs']),12)
        self.assertEqual(len(set(j['id'] for j in status['wiki_jobs'])),12)

    def test_real_wiki_path_records_both_light_and_main_without_prompt_data(self):
        self.pdf();self.scanner.scan();process_next(self.db)
        main,light=self.client(),self.client('small',True)
        main._wiki_extractor=light
        def response(request,**kwargs):
            payload=json.loads(json.loads(request.data)['messages'][1]['content'])
            body=json.dumps(wiki_fixtures.FakeLLM().sections(payload['evidence']))
            return self.response({'prompt_tokens':40,'completion_tokens':8},body)
        with patch('backend.llm_client.build_opener') as opener:
            opener.return_value.open.side_effect=response
            result=run_jobs(self.db,main)
        self.assertNotEqual(result.get('status'),'failed')
        status=processing_status(self.db)
        u=status['wiki_jobs'][0]['usage']
        self.assertEqual({m['model'] for m in u['models']},{'main','small'})
        self.assertEqual(u['total_tokens'],48*u['calls'])
        self.assertTrue(all(m['role']=='light' for m in u['models'] if m['model']=='small'))
