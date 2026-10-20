"""Evidence-first requests, scoped reuse, and cancellation using fixture models."""
import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import research_requests as requests
from backend import answer_cache
from backend.auth import principal_context,public_user
from backend.identity_settings import bootstrap_admin_id
from backend.pilot_db import connect,now
from backend.task_queue import ensure_schema,run_next,get_task,WorkCancelled
from backend.wiki_api import Question,recommendations,score,request_topic,TopicRequest
from backend.wiki_answer import answer
from backend.answer_trace import read_run
import test_markdown_wiki as fixtures
from test_research_answers import ResearchLLM
from test_pilot import make_pdf,TEXT
from backend.pilot_ingest import ingest_file
from backend.main import app


class Checkpoints:
    def __init__(self,stop_at=None):
        self.checkpoint_data={}
        self.stop_at=stop_at
    def check_cancelled(self):
        pass
    def checkpoint(self,data):
        self.checkpoint_data=json.loads(json.dumps(data))
        if data.get('phase')==self.stop_at:
            self.stop_at=None
            raise WorkCancelled('simulated worker interruption')


class ResearchRequestTests(unittest.TestCase):
    curation = fixtures.MarkdownWikiTests.curation
    sync = fixtures.MarkdownWikiTests.sync

    def setUp(self):
        fixtures.MarkdownWikiTests.setUp(self)
        self.sync()
        with connect(self.db) as db:
            requests.ensure_schema(db);ensure_schema(db)
            db.execute("INSERT INTO users(employee_id,role,created_at,updated_at) VALUES('300','user',?,?)",(now(),now()))
            db.commit()
        requests.register_handlers()

    def tearDown(self):
        fixtures.MarkdownWikiTests.tearDown(self)

    @contextmanager
    def user(self,uid):
        with connect(self.db) as db:
            user = public_user(db.execute('SELECT * FROM users WHERE employee_id=?',(uid,)).fetchone())
        token = principal_context.set(user)
        try:
            yield
        finally:
            principal_context.reset(token)

    def submit(self,**values):
        return requests.submit_request(requests.Submission(**(dict(question='refresh 74',expert_ids=['dram'])|values)),self.db)

    def finish(self,llm=None,**values):
        model = llm or ResearchLLM()
        with patch('backend.research_requests.CompanyLLM',return_value=model):
            row = self.submit(**values)
            self.assertTrue(run_next(self.db,kinds=['research_answer']))
        return requests.read_request(row['id'],self.db),model

    def test_submit_returns_local_evidence_without_remote_calls_and_deduplicates(self):
        with patch('backend.llm_client.CompanyLLM.complete_json',side_effect=AssertionError('remote LLM')), \
             patch('backend.embedding_client.EmbeddingClient.embed',side_effect=AssertionError('remote embedding')), \
             patch('backend.wiki_retrieval.rerank',side_effect=AssertionError('remote rerank')):
            result = requests.submit_request(requests.Submission(question='refresh 74'),self.db)
            repeated = requests.submit_request(requests.Submission(question='refresh 74'),self.db)
            routed = recommendations('refresh 74',ResearchLLM(),database=self.db)
        self.assertEqual(result['id'],repeated['id'])
        self.assertEqual(result['status'],'queued')
        self.assertIsNone(result['result'])
        self.assertTrue(result['local_evidence']['claims'])
        self.assertEqual(result['local_evidence']['research']['validation_status'],'not_generated')
        self.assertTrue(routed['experts'])
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM work_tasks WHERE kind='research_answer'").fetchone()[0],1)

    def test_prepared_packet_publication_guard_blocks_revoked_shared_input(self):
        checks=[]
        def revoked(db):
            checks.append(db.in_transaction)
            return False
        with self.assertRaises(WorkCancelled):
            answer_cache.prepare_case('refresh 74',['dram'],database=self.db,local_only=True,
                                      publication_guard=revoked)
        self.assertEqual(checks,[True])
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM prepared_research_cases').fetchone()[0],0)

    def test_followup_context_survives_worker_and_stays_private_outside_team_learning(self):
        from backend.knowledge_learning import feedback,Feedback
        first,_=self.finish()
        prior=first['result']['run_id']
        followup,llm=self.finish(question='그 결과의 적용 조건은?',history_run_ids=[prior],visibility='team')
        self.assertEqual(followup['visibility'],'private')
        self.assertFalse(followup['learning_eligible'])
        self.assertEqual(followup['history_run_ids'],[prior])
        self.assertEqual(llm.operations,['plan','refine','answer','review'])
        self.assertTrue(all(call['conversation_context'][0]['question']=='refresh 74' for call in llm.calls))
        self.assertTrue(any('이전 질문의 주제' in query for query in followup['result']['research']['queries']))
        with connect(self.db) as db:
            self.assertFalse(db.execute('SELECT 1 FROM learning_origins WHERE target=?',(followup['id'],)).fetchone())
        result=feedback(Feedback(request_id=followup['id'],kind='incorrect',comment='Private dialogue condition'))
        self.assertFalse(result['used_for_team_learning'])
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT visibility FROM learning_feedback WHERE id=?',(result['id'],)).fetchone()[0],'private')
        with self.assertRaises(HTTPException) as error:
            requests.change_visibility(followup['id'],requests.Visibility(visibility='team'))
        self.assertEqual(error.exception.status_code,409)
        with self.user('300'):
            with self.assertRaises(HTTPException) as error:requests.status(followup['id'])
            self.assertEqual(error.exception.status_code,404)
            self.assertNotIn(followup['id'],[r['id'] for r in requests.list_requests(visibility='team')['requests']])
            with self.assertRaises(HTTPException) as error:
                self.submit(history_run_ids=[prior])
            self.assertEqual(error.exception.status_code,404)

    def test_contextual_cache_requires_same_owned_history_and_never_uses_public_prepared_case(self):
        first,_=self.finish(question='retention')
        second,_=self.finish(question='refresh')
        answer_cache.prepare_case('refresh 74',['dram'],database=self.db,llm=ResearchLLM())
        prior=first['result']['run_id']
        row,llm=self.finish(history_run_ids=[prior])
        self.assertEqual(llm.operations,['plan','refine','answer','review'])
        with patch('backend.research_requests.CompanyLLM',return_value=llm):
            cached=self.submit(history_run_ids=[prior])
            plain=self.submit()
            other=self.submit(history_run_ids=[second['result']['run_id']])
        self.assertTrue(cached['cache_hit'])
        self.assertNotEqual(cached['result']['run_id'],row['result']['run_id'])
        self.assertFalse(plain['cache_hit'])
        self.assertFalse(other['cache_hit'])
        self.assertNotEqual(plain['id'],other['id'])

    def test_report_context_is_owned_revision_bound_and_excluded_from_learning(self):
        from backend.report_store import create_report,save_report
        report=create_report('Private report','Internal condition discussion')
        fields=dict(report_id=report['id'],report_section_id=report['document']['sections'][0]['id'],report_revision=1)
        row,llm=self.finish(visibility='team',**fields)
        self.assertEqual(row['visibility'],'private')
        self.assertFalse(row['learning_eligible'])
        self.assertIn('Private report',llm.calls[0]['conversation_context'][0]['question'])
        with connect(self.db) as db:
            self.assertFalse(db.execute('SELECT 1 FROM learning_origins WHERE target=?',(row['id'],)).fetchone())
        with self.assertRaises(HTTPException) as error:
            requests.change_visibility(row['id'],requests.Visibility(visibility='team'))
        self.assertEqual(error.exception.status_code,409)
        with self.user('300'):
            with self.assertRaises(HTTPException) as error:requests.status(row['id'])
            self.assertEqual(error.exception.status_code,404)
            with self.assertRaises(HTTPException) as error:self.submit(**fields)
            self.assertEqual(error.exception.status_code,404)
        with patch('backend.research_requests.CompanyLLM',return_value=llm):
            self.assertTrue(self.submit(**fields)['cache_hit'])
        save_report(report['id'],1,report['document'],'Fixture revision')
        with self.assertRaises(HTTPException) as error:self.submit(**fields)
        self.assertEqual(error.exception.status_code,409)
        with patch('backend.research_requests.CompanyLLM',return_value=llm):
            fresh=self.submit(**(fields|{'report_revision':2}))
        self.assertFalse(fresh['cache_hit'])

    def test_shared_active_request_deduplicates_after_its_origin_changes_cache_version(self):
        before=answer_cache.validity(['dram'],database=self.db)
        row=self.submit(visibility='team')
        self.assertNotEqual(answer_cache.validity(['dram'],database=self.db),before)
        repeated=self.submit(visibility='team')
        self.assertEqual(repeated['id'],row['id'])
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM work_tasks WHERE kind='research_answer'").fetchone()[0],1)
        # Visibility remains part of request identity; a private question must
        # not be silently joined to the shared request.
        self.assertNotEqual(self.submit(visibility='private')['id'],row['id'])

    def test_local_routing_prioritizes_available_original_papers_over_generic_profiles(self):
        with connect(self.db) as db:
            db.execute("UPDATE expert_categories SET overview='model estimate simulation integration at at at retention' WHERE id='modeling'")
            db.execute("UPDATE expert_categories SET overview='retention refresh power model estimate at at' WHERE id='ferroelectric'")
            db.commit()
        result=recommendations('OCTRAM retention at 90 C and refresh power estimate',database=self.db)
        self.assertEqual(result['experts'][0]['id'],'dram')
        self.assertTrue(all(e['matching_papers']>0 for e in result['experts']))
        self.assertNotIn('at',result['terms'])
        self.assertNotIn('C',result['terms'])
        self.assertEqual(score('simulation integration estimate',['at']),0)
        result=requests.submit_request(requests.Submission(question='OCTRAM retention at 90 C and refresh power estimate'),self.db)
        self.assertIn('dram',result['expert_ids'])
        self.assertGreater(result['local_evidence']['research']['paper_count'],0)

    def test_topic_request_enqueues_only_selected_source_synthesis(self):
        source=next(row for row in self.store.rows() if row['kind']=='source')
        with connect(self.db) as db:
            before=db.execute('SELECT count(*) FROM wiki_jobs').fetchone()[0]
        result=request_topic('dram',TopicRequest(title='Refresh trade-offs',source_ids=[source['id']]))
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM wiki_jobs').fetchone()[0],before)
            row=db.execute('SELECT * FROM knowledge_syntheses WHERE id=?',(result['id'],)).fetchone()
            expected=json.loads(source['refs_json'])['paper_ids']
            self.assertEqual(json.loads(row['paper_ids_json']),expected)
            self.assertEqual(row['expert_id'],'dram')
            self.assertEqual(db.execute('SELECT kind FROM work_tasks WHERE id=?',(result['job_id'],)).fetchone()[0],'knowledge.publish_synthesis')

    def test_completed_answer_reuse_creates_new_owner_snapshot_and_citations(self):
        first,llm = self.finish()
        self.assertEqual(llm.operations,['plan','refine','answer','review'])
        with patch('backend.research_requests.CompanyLLM',return_value=llm):
            second = self.submit()
        self.assertEqual(second['status'],'completed')
        self.assertTrue(second['cache_hit'])
        self.assertNotEqual(first['result']['run_id'],second['result']['run_id'])
        second_run = read_run(second['result']['run_id'],self.db)
        self.assertIn('/answer-runs/'+second_run['id']+'/claims/',second_run['result']['synthesis']['markdown'])
        self.assertNotIn(first['result']['run_id'],second_run['result']['synthesis']['markdown'])
        self.assertIn('/research-requests/'+second['id']+'/claims/',second['result']['synthesis']['markdown'])
        self.assertEqual(second_run['trace']['model_calls'],[])
        self.assertEqual(second_run['owner_id'],bootstrap_admin_id())

    def test_ready_packet_runs_answer_and_review_only_and_is_not_an_answer(self):
        planner=ResearchLLM()
        prepared = answer_cache.prepare_case('refresh 74',['dram'],owner_id=bootstrap_admin_id(),database=self.db,llm=planner)
        self.assertEqual(prepared['status'],'evidence_ready')
        self.assertEqual(planner.operations,['plan','refine'])
        row,llm = self.finish()
        self.assertEqual(llm.operations,['answer','review'])
        self.assertEqual(row['result']['research']['validation_status'],'model_reviewed')
        self.assertEqual(read_run(row['result']['run_id'],self.db)['trace']['prepared_case_id'],prepared['id'])

    def test_private_prepared_packet_and_final_answer_are_not_reused_by_other_owner(self):
        answer_cache.prepare_case('refresh 74',['dram'],owner_id=bootstrap_admin_id(),database=self.db,llm=ResearchLLM())
        first,_ = self.finish()
        with self.user('300'):
            self.assertIsNone(answer_cache.read_prepared_case('refresh 74',['dram'],'300',self.db))
            second,llm = self.finish()
            self.assertEqual(llm.operations,['plan','refine','answer','review'])
            self.assertFalse(second['cache_hit'])
            with self.assertRaises(HTTPException):
                requests.read_request(first['id'],self.db)
            self.assertEqual(read_run(second['result']['run_id'],self.db)['owner_id'],'300')

    def test_source_scope_model_and_policy_changes_invalidate_exact_cache(self):
        first,llm = self.finish()
        request = Question(question='refresh 74',expert_ids=['dram'])
        self.assertIsNone(answer_cache.reuse_answer(request,owner_id=bootstrap_admin_id(),model_identity='new-deployment',database=self.db))
        with patch('backend.answer_cache.VERSION','new-validator-policy'):
            self.assertIsNone(answer_cache.reuse_answer(request,owner_id=bootstrap_admin_id(),model_identity=llm.identity,database=self.db))
        with connect(self.db) as db:
            db.execute("UPDATE expert_categories SET overview=overview||' updated scope' WHERE id='dram'");db.commit()
        self.assertIsNone(answer_cache.reuse_answer(request,owner_id=bootstrap_admin_id(),model_identity=llm.identity,database=self.db))
        # A new candidate invalidates a conclusion even if its old cited claim is unchanged.
        with connect(self.db) as db:
            db.execute('UPDATE wiki_corpus_clock SET revision=revision+1 WHERE id=1');db.commit()
        self.assertIsNone(answer_cache.reuse_answer(request,owner_id=bootstrap_admin_id(),model_identity=llm.identity,database=self.db))
        self.assertEqual(read_run(first['result']['run_id'],self.db)['evidence']['claim:claim1']['claim']['id'],'claim1')

    def test_different_numeric_conditions_never_share_exact_cache(self):
        _,llm = self.finish()
        for question in ('refresh 74 ms','refresh 74 s','refresh 75'):
            self.assertIsNone(answer_cache.reuse_answer(Question(question=question,expert_ids=['dram']),owner_id=bootstrap_admin_id(),model_identity=llm.identity,database=self.db))

    def test_cache_versions_include_public_demand_inputs_but_exclude_private_feedback_and_outputs(self):
        from backend.knowledge_learning import _add_question
        with connect(self.db) as db:
            qid=_add_question(db,'dram','refresh 74',origin='shared',target='shared-input',actor=bootstrap_admin_id())
            db.execute('INSERT INTO learning_feedback VALUES(?,?,?,?,?,?,?)',
                       ('public-input','shared-input',bootstrap_admin_id(),'incorrect','Check the retention conditions','team',now()))
            db.commit()
        before=answer_cache.validity(['dram'],database=self.db)
        with connect(self.db) as db:
            db.execute('INSERT INTO learning_feedback VALUES(?,?,?,?,?,?,?)',
                       ('private-input','shared-input',bootstrap_admin_id(),'incorrect','Private feedback must stay private','private',now()))
            db.execute("UPDATE learning_questions SET state='answer_validated',last_result_json=? WHERE id=?",('{"output":"changed"}',qid))
            _add_question(db,'nand','unrelated shared question',origin='shared',target='other-scope',actor='300')
            db.commit()
        self.assertEqual(answer_cache.validity(['dram'],database=self.db),before)
        with connect(self.db) as db:
            db.execute("UPDATE learning_feedback SET comment='Recheck the temperature comparison' WHERE id='public-input'")
            db.commit()
        changed=answer_cache.validity(['dram'],database=self.db)
        self.assertNotEqual(changed,before)
        with connect(self.db) as db:
            db.execute("UPDATE learning_origins SET active=0 WHERE question_id=?",(qid,))
            db.commit()
        self.assertNotEqual(answer_cache.validity(['dram'],database=self.db),changed)

    def test_public_feedback_and_revocation_prevent_inflight_answer_from_repopulating_cache(self):
        row,llm=self.finish(visibility='team')
        request=Question(question=row['question'],expert_ids=row['expert_ids'])
        def fingerprint():
            return answer_cache.validity(request.expert_ids,database=self.db,model_identity=llm.identity)
        before=fingerprint()
        with connect(self.db) as db:
            db.execute('INSERT INTO learning_feedback VALUES(?,?,?,?,?,?,?)',
                       ('late-feedback',row['id'],bootstrap_admin_id(),'incorrect','Recheck source conditions','team',now()))
            db.execute('DELETE FROM research_answer_cache')
            db.commit()
        self.assertFalse(answer_cache.remember_answer(request,row['result'],owner_id=row['owner_id'],
            model_identity=llm.identity,expected_hash=before,database=self.db))
        before=fingerprint()
        requests.change_visibility(row['id'],requests.Visibility(visibility='private'))
        self.assertFalse(answer_cache.remember_answer(request,row['result'],owner_id=row['owner_id'],
            model_identity=llm.identity,expected_hash=before,database=self.db))
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM research_answer_cache').fetchone()[0],0)

    def test_new_uncited_paper_invalidates_cached_conclusion(self):
        _,llm=self.finish()
        new=self.root/'counterexample.pdf'
        make_pdf(new,TEXT+' A different experiment reported a contradictory result.')
        ingest_file(new,self.db,0)
        self.assertIsNone(answer_cache.reuse_answer(Question(question='refresh 74',expert_ids=['dram']),
            owner_id=bootstrap_admin_id(),model_identity=llm.identity,database=self.db))

    def test_local_only_preparation_never_claims_investigation_completed(self):
        llm=ResearchLLM()
        with patch('backend.embedding_client.EmbeddingClient.embed',side_effect=AssertionError('remote embedding')):
            prepared=answer_cache.prepare_case('refresh 74',['dram'],database=self.db,llm=llm,local_only=True)
        self.assertFalse(prepared['investigation_complete'])
        self.assertEqual(llm.operations,[])
        self.assertIsNone(answer_cache.read_prepared_case('refresh 74',['dram'],bootstrap_admin_id(),self.db))

    def test_preparation_resumes_after_each_model_call(self):
        context=Checkpoints('prepared_plan')
        first=ResearchLLM()
        with self.assertRaises(WorkCancelled):
            answer_cache.prepare_case('refresh 74',['dram'],database=self.db,llm=first,context=context)
        second=ResearchLLM()
        ready=answer_cache.prepare_case('refresh 74',['dram'],database=self.db,llm=second,context=context)
        self.assertEqual(first.operations,['plan'])
        self.assertEqual(second.operations,['refine'])
        self.assertTrue(ready['investigation_complete'])

    def test_answer_resumes_generated_output_and_runs_remaining_review(self):
        answer_cache.prepare_case('refresh 74',['dram'],database=self.db,llm=ResearchLLM())
        ready=answer_cache.read_prepared_case('refresh 74',['dram'],bootstrap_admin_id(),self.db)
        request=Question(question='refresh 74',expert_ids=['dram'])
        context=Checkpoints('answer_completed')
        first=ResearchLLM()
        with self.assertRaises(WorkCancelled):
            answer(request,first,database=self.db,prepared=ready,context=context)
        second=ResearchLLM()
        result=answer(request,second,database=self.db,prepared=ready,context=context)
        self.assertEqual(first.operations,['answer'])
        self.assertEqual(second.operations,['review'])
        self.assertEqual(result['research']['validation_status'],'model_reviewed')

    def test_precomputed_faq_is_validated_then_reused_only_for_its_owner(self):
        answer_cache.prepare_case('refresh 74',['dram'],database=self.db,llm=ResearchLLM())
        llm=ResearchLLM()
        result=answer_cache.precompute_answer('refresh 74',['dram'],owner_id=bootstrap_admin_id(),database=self.db,llm=llm)
        self.assertEqual(result['status'],'answer_validated')
        self.assertEqual(llm.operations,['answer','review'])
        with patch('backend.research_requests.CompanyLLM',return_value=llm):
            response=self.submit()
        self.assertTrue(response['cache_hit'])
        with connect(self.db) as db:
            metric=db.execute('SELECT * FROM research_request_metrics WHERE request_id=?',(response['id'],)).fetchone()
        self.assertGreaterEqual(metric['initial_ms'],0)
        self.assertEqual(metric['initial_ms'],metric['final_ms'])

    def test_cancelled_request_never_runs_or_publishes(self):
        result = self.submit()
        cancelled = requests.cancel(result['id'])
        self.assertEqual(cancelled['status'],'cancelled')
        self.assertFalse(run_next(self.db,kinds=['research_answer']))
        self.assertIsNone(requests.read_request(result['id'],self.db)['result'])

    def test_http_submit_poll_share_and_cancel_contract(self):
        client=TestClient(app)
        try:
            with patch('backend.llm_client.CompanyLLM.complete_json',side_effect=AssertionError('submit must be local')):
                response=client.post('/api/pilot/research-requests',json={'question':'refresh 74','expert_ids':['dram'],'visibility':'private'})
            self.assertEqual(response.status_code,200,response.text)
            row=response.json()
            self.assertEqual(row['status'],'queued')
            self.assertTrue(row['is_owner'])
            self.assertEqual(client.get('/api/pilot/research-requests/'+row['id']).json()['id'],row['id'])
            shared=client.patch('/api/pilot/research-requests/'+row['id']+'/visibility',json={'visibility':'team'})
            self.assertEqual(shared.json()['visibility'],'team')
            self.assertIn(row['id'],[r['id'] for r in client.get('/api/pilot/research-requests?visibility=team').json()['requests']])
            self.assertEqual(client.post('/api/pilot/research-requests/'+row['id']+'/cancel').json()['status'],'cancelled')
        finally:
            client.close()

    def test_request_list_total_uses_same_owner_and_visibility_scope_beyond_page_limit(self):
        with connect(self.db) as db:
            for owner,visibility,count in [(bootstrap_admin_id(),'private',55),(bootstrap_admin_id(),'team',3),('300','private',4),('300','team',5)]:
                for index in range(count):
                    db.execute('''INSERT INTO research_requests
                      (id,owner_id,question,expert_ids_json,expert_names_json,visibility,status,phase,input_hash,local_evidence_json,created_at,updated_at)
                      VALUES(?,?,?,'["dram"]','["DRAM"]',?,'completed','completed','fixture','{}',?,?)''',
                      (f'{owner}-{visibility}-{index}',owner,'fixture question',visibility,now(),now()))
            db.commit()
        own=requests.list_requests(mine=True)
        self.assertEqual((own['total'],len(own['requests'])),(58,50))
        self.assertTrue(all(row['owner_id']==bootstrap_admin_id() for row in own['requests']))
        compact=requests.list_requests(mine=True,summary=True)
        self.assertEqual((compact['total'],len(compact['requests'])),(58,50))
        self.assertTrue(all('result' not in row and 'local_evidence' not in row for row in compact['requests']))
        self.assertTrue(all(row['is_owner'] for row in compact['requests']))
        private=requests.list_requests(visibility='private',limit=2)
        self.assertEqual((private['total'],len(private['requests'])),(55,2))
        shared=requests.list_requests(visibility='team',limit=2)
        self.assertEqual((shared['total'],len(shared['requests'])),(8,2))
        self.assertTrue(all(row['visibility']=='team' for row in shared['requests']))
        with self.user('300'):
            other=requests.list_requests(mine=True,limit=100)
        self.assertEqual((other['total'],len(other['requests'])),(9,9))

    def test_expired_failed_work_is_reconciled_without_running_handler(self):
        result=self.submit()
        row=requests.read_request(result['id'],self.db)
        with connect(self.db) as db:
            db.execute("UPDATE research_requests SET status='running' WHERE id=?",(row['id'],))
            db.execute("UPDATE work_tasks SET state='failed',attempts=max_attempts,error='lease expired' WHERE id=?",(row['job_id'],))
            db.commit()
        recovered=requests.read_request(row['id'],self.db)
        self.assertEqual(recovered['status'],'failed')
        self.assertTrue(recovered['error'])
        self.assertEqual(requests.list_requests()['requests'][0]['status'],'failed')

    def test_cancellation_during_model_call_prevents_publication(self):
        holder = {}
        def stop(payload,result):
            requests.cancel(holder['id'])
            return result
        llm = ResearchLLM(plan=stop)
        with patch('backend.research_requests.CompanyLLM',return_value=llm):
            holder.update(self.submit())
            self.assertTrue(run_next(self.db,kinds=['research_answer']))
        row = requests.read_request(holder['id'],self.db)
        self.assertEqual(row['status'],'cancelled')
        self.assertIsNone(row['result'])
        self.assertEqual(llm.operations,['plan'])
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM answer_runs').fetchone()[0],0)

    def test_sharing_grants_snapshot_only_and_revocation_removes_access(self):
        row,_ = self.finish(visibility='team')
        with self.user('300'):
            self.assertEqual(requests.read_request(row['id'],self.db)['visibility'],'team')
            self.assertEqual(requests.shared_claim(row['id'],'claim1')['id'],'claim1')
            with self.assertRaises(HTTPException):
                requests.cancel(row['id'])
            with self.assertRaises(HTTPException):
                read_run(row['result']['run_id'],self.db)
        requests.change_visibility(row['id'],requests.Visibility(visibility='private'))
        with self.user('300'):
            with self.assertRaises(HTTPException):
                requests.shared_claim(row['id'],'claim1')

    def test_unconfigured_model_finishes_with_honest_evidence_only_status(self):
        row = self.submit()
        self.assertTrue(run_next(self.db,kinds=['research_answer']))
        result = requests.read_request(row['id'],self.db)['result']
        self.assertEqual(result['synthesis']['mode'],'evidence')
        self.assertEqual(result['research']['validation_status'],'not_generated')
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM research_answer_cache').fetchone()[0],0)


if __name__ == '__main__':
    unittest.main()
