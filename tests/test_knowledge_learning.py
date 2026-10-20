import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_pilot as fixtures
from backend.pilot_curate import apply_curation
from backend.identity_settings import bootstrap_admin_id
from backend.pilot_db import connect
from backend import knowledge_learning as learning
from backend.answer_cache import scope_key


class LearningTests(unittest.TestCase):
    curation = fixtures.PilotTests.curation

    def setUp(self):
        fixtures.PilotTests.setUp(self)
        self.env = patch.dict(os.environ, {'EVIDENCE_DB_PATH': str(self.db), 'AUTH_MODE': 'local_admin',
            'LLM_BASE_URL': '', 'LLM_API_KEY': '', 'LLM_MODEL': '', 'LLM_BACKGROUND_BASE_URL': '',
            'LLM_BACKGROUND_API_KEY': '', 'LLM_BACKGROUND_MODEL': '', 'EMBEDDING_BASE_URL': '',
            'EMBEDDING_API_KEY': '', 'EMBEDDING_MODEL': '', 'RERANKER_BASE_URL': ''})
        self.env.start()
        target, _ = self.curation()
        apply_curation(target, self.db)
        self.model = patch('backend.llm_client.CompanyLLM', return_value=SimpleNamespace(configured=False, identity='fixture'))
        self.model.start()

    def tearDown(self):
        self.model.stop()
        self.env.stop()
        fixtures.PilotTests.tearDown(self)

    def question(self, db, text='How does refresh change?', origin='planned', target='plan-1'):
        return learning._add_question(db, 'dram', text, origin=origin, target=target, actor=bootstrap_admin_id())

    def test_private_questions_never_become_team_learning_inputs(self):
        with connect(self.db) as db:
            learning.observe_research_request(db, {'id':'private', 'visibility':'private', 'question':'Confidential experiment', 'expert_ids':['dram']})
            self.assertEqual(db.execute('SELECT count(*) FROM learning_questions').fetchone()[0], 0)
            self.assertEqual(learning.enqueue_daily_work(db), [])

    def test_sharing_reversal_removes_prepared_question_and_scope(self):
        request = {'id':'shared', 'visibility':'team', 'question':'How does refresh change?', 'expert_ids':['dram'], 'owner_id':bootstrap_admin_id()}
        with connect(self.db) as db:
            learning.observe_research_request(db, request)
            qid = db.execute('SELECT id FROM learning_questions').fetchone()[0]
            db.execute('INSERT INTO prepared_research_cases VALUES(?,?,?,?,?,?,?,?,?,?)',
                       ('case', '', request['question'], scope_key(['dram']), '["dram"]', 'v', '{}', '[]', 'evidence_ready', '2026'))
            learning.observe_research_request(db, dict(request, visibility='private'))
            self.assertEqual(db.execute('SELECT state FROM learning_questions WHERE id=?', (qid,)).fetchone()[0], 'inactive')
            self.assertEqual(db.execute('SELECT count(*) FROM prepared_research_cases').fetchone()[0], 0)
            self.assertEqual(learning.enqueue_daily_work(db), [])

    def test_independent_planned_demand_survives_share_reversal(self):
        request = {'id':'shared', 'visibility':'team', 'question':'How does refresh change?', 'expert_ids':['dram']}
        with connect(self.db) as db:
            learning.observe_research_request(db, request)
            qid = self.question(db)
            learning.observe_research_request(db, dict(request, visibility='private'))
            self.assertTrue(learning._available(db, qid))
            self.assertNotEqual(db.execute('SELECT state FROM learning_questions WHERE id=?', (qid,)).fetchone()[0], 'inactive')

    def test_surviving_plan_does_not_retain_withdrawn_feedback_packet_or_caches(self):
        request = {'id':'shared', 'visibility':'team', 'question':'How does refresh change?', 'expert_ids':['dram']}
        with connect(self.db) as db:
            learning.observe_research_request(db, request)
            qid = self.question(db)
            db.execute('INSERT INTO learning_feedback VALUES(?,?,?,?,?,?,?)',
                       ('feedback','shared',bootstrap_admin_id(),'incorrect','Private after reversal','team','2026'))
            old_origin = learning._origin_fingerprint(db, qid)
            db.execute('INSERT INTO prepared_research_cases VALUES(?,?,?,?,?,?,?,?,?,?)',
                       ('case','',request['question'],scope_key(['dram']),'["dram"]','v','{}','[]','evidence_ready','2026'))
            for actor in ('owner-a','owner-b'):
                db.execute('INSERT INTO research_answer_cache VALUES(?,?,?,?,?,?)',
                           (actor,request['question'],scope_key(['dram']),'v','run-'+actor,'2026'))
            jobs = learning.enqueue_daily_work(db)
            learning.observe_research_request(db, dict(request,visibility='private'))
            self.assertTrue(learning._available(db,qid))
            self.assertNotEqual(old_origin,learning._origin_fingerprint(db,qid))
            self.assertEqual(db.execute('SELECT count(*) FROM prepared_research_cases').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM research_answer_cache').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT state FROM work_tasks WHERE id=?',(jobs[0],)).fetchone()[0],'cancelled')
            self.assertEqual(len(learning.enqueue_daily_work(db)),1)

    def test_scientific_case_and_exponents_are_not_merged(self):
        with connect(self.db) as db:
            ids = [self.question(db, text, target=str(i)) for i, text in enumerate(['At 1 mA?', 'At 1 MA?', 'At 10² s?', 'At 102 s?'])]
            self.assertEqual(len(set(ids)), 4)

    def test_generated_question_quota_is_five_per_expert_day_and_deduplicated(self):
        with connect(self.db) as db:
            for i in range(5):
                self.assertTrue(learning._reserve(db, 'dram', 'new', str(i)))
            self.assertIsNone(learning._reserve(db, 'dram', 'new', '6'))
            self.assertIsNone(learning._reserve(db, 'dram', 'new', '1'))
            self.assertTrue(learning._reserve(db, 'nand', 'new', '1'))
            with patch('backend.knowledge_learning._day', return_value='2099-01-01'):
                self.assertIsNone(learning._reserve(db, 'dram', 'new', '1'))
                self.assertTrue(learning._reserve(db, 'dram', 'new', 'new-source'))

    def test_two_repairs_are_per_question_and_material_source_version(self):
        with connect(self.db) as db:
            first = self.question(db)
            second = self.question(db, 'What temperature?', target='second')
            for qid in (first, second):
                for i in range(2):
                    self.assertTrue(learning._reserve(db, 'dram', 'repair', qid + str(i), question_id=qid, source_hash='source-v1'))
                self.assertIsNone(learning._reserve(db, 'dram', 'repair', qid + 'third', question_id=qid, source_hash='source-v1'))
                self.assertTrue(learning._reserve(db, 'dram', 'repair', qid + 'new', question_id=qid, source_hash='source-v2'))

    def test_planned_questions_do_not_starve_behind_automatic_daily_quota(self):
        with connect(self.db) as db:
            for i in range(5):
                learning._reserve(db, 'dram', 'new', str(i))
            self.question(db)
            jobs = learning.enqueue_daily_work(db)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(learning.enqueue_daily_work(db), [])
            payload = json.loads(db.execute('SELECT payload_json FROM work_tasks WHERE id=?', (jobs[0],)).fetchone()[0])
            db.execute('UPDATE learning_questions SET last_input_hash=?', (payload['input_hash'],))
            self.assertEqual(learning.enqueue_daily_work(db), [])
            db.execute("UPDATE claims SET scope='Updated source condition' WHERE id='claim1'")
            self.assertEqual(len(learning.enqueue_daily_work(db)), 1)

    def test_learning_outputs_do_not_invalidate_canonical_input(self):
        with connect(self.db) as db:
            before = learning.source_fingerprint(db, 'dram')
            qid = self.question(db)
            db.execute('INSERT INTO learning_events VALUES(?,?,?,?,?,?)', ('event','dram',qid,'evidence_prepared','Prepared','2026'))
            self.assertEqual(before, learning.source_fingerprint(db, 'dram'))
            db.execute("UPDATE pages SET text=text||' New source material.'")
            self.assertNotEqual(before, learning.source_fingerprint(db, 'dram'))

    def test_model_configuration_changes_investigation_without_resetting_source_budget(self):
        with connect(self.db) as db:
            qid = self.question(db)
            source_hash = learning.source_fingerprint(db,'dram')
            jobs = learning.enqueue_daily_work(db)
            old = json.loads(db.execute('SELECT payload_json FROM work_tasks WHERE id=?',(jobs[0],)).fetchone()[0])
            db.execute('UPDATE learning_questions SET last_input_hash=? WHERE id=?',(old['input_hash'],qid))
            with patch.dict(os.environ, {'LLM_BACKGROUND_MODEL':'configured-later'}):
                self.assertEqual(source_hash,learning.source_fingerprint(db,'dram'))
                self.assertEqual(len(learning.enqueue_daily_work(db)),1)
            self.assertEqual(db.execute('SELECT count(*) FROM learning_repairs WHERE question_id=? AND source_hash=?',(qid,source_hash)).fetchone()[0],1)

    def test_questioner_rejects_invented_sources_without_registering_question(self):
        with connect(self.db) as db:
            fingerprint = learning.source_fingerprint(db, 'dram')
        context = SimpleNamespace(database=self.db, check_cancelled=lambda:None)
        model = SimpleNamespace(complete_json=lambda *a:{'questions':[{'question':'An invented claim?', 'evidence_ids':['invented']}]})
        with patch('backend.llm_client.CompanyLLM', return_value=model):
            with self.assertRaises(ValueError):
                learning._questioner({'expert_id':'dram','source_hash':fingerprint,'limit':5}, context)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM learning_questions').fetchone()[0], 0)

    def test_evidence_search_gap_is_not_fabricated_answer(self):
        with connect(self.db) as db:
            qid = self.question(db)
            fingerprint = learning.source_fingerprint(db, 'dram')
            db.commit()
        context = SimpleNamespace(database=self.db, check_cancelled=lambda:None)
        with patch('backend.answer_cache.prepare_case', return_value={'id':'case','status':'needs_evidence'}):
            result = learning._evaluate({'question_id':qid,'source_hash':fingerprint,'input_hash':'material'}, context)
        self.assertEqual(result['status'], 'needs_evidence')
        with connect(self.db) as db:
            self.assertIn('retrieval_gap', {r[0] for r in db.execute('SELECT kind FROM learning_gaps')})
            self.assertEqual(db.execute('SELECT state FROM learning_questions').fetchone()[0], 'needs_evidence')

    def test_gap_categories_use_source_state_and_explicit_investigation_signals(self):
        with connect(self.db) as db:
            question = {'id':'test','expert_id':'dram','question':'Compare measurements'}
            gaps,_ = learning._gap_assessment(db,question,{'status':'needs_evidence'}, {})
            self.assertIn('extraction_gap',gaps)
            self.assertIn('retrieval_gap',gaps)
            self.assertNotIn('conflict_gap',gaps)
            self.assertNotIn('condition_gap',gaps)
            bundle = {'evidence':{'claim':{'kind':'claim','claim':{'measurements':[{'conditions':[{'name':'temperature','raw_value':None}]}]}}},
                      'investigation_gaps':['Possible conflict across reported outcomes.']}
            gaps,_ = learning._gap_assessment(db,question,{'status':'evidence_ready'},bundle,[{'kind':'incorrect','comment':'Check the answer'}])
            self.assertIn('condition_gap',gaps)
            self.assertIn('conflict_gap',gaps)
            self.assertIn('과제',gaps['conflict_gap'])
            self.assertNotIn('missing_sources',gaps)

    def test_no_assigned_sources_waits_for_new_material_without_model_call(self):
        with connect(self.db) as db:
            qid = learning._add_question(db,'nand','Which source should we use?',origin='planned',target='plan',actor=bootstrap_admin_id())
            db.execute("DELETE FROM expert_papers WHERE expert_id='nand'")
            source_hash = learning.source_fingerprint(db,'nand')
            db.commit()
        with patch('backend.answer_cache.prepare_case') as prepare:
            result = learning._evaluate({'question_id':qid,'source_hash':source_hash,'input_hash':'material'},
                                        SimpleNamespace(database=self.db,check_cancelled=lambda:None))
            prepare.assert_not_called()
        self.assertEqual(result['status'],'needs_evidence')
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT kind FROM learning_gaps WHERE question_id=?',(qid,)).fetchone()[0],'missing_sources')

    def test_source_preparation_followup_is_one_bounded_dependency(self):
        from backend.task_queue import enqueue_task
        with connect(self.db) as db:
            qid = self.question(db)
            question = dict(db.execute('SELECT * FROM learning_questions WHERE id=?',(qid,)).fetchone())
            pid = db.execute("SELECT paper_id FROM expert_papers WHERE expert_id='dram'").fetchone()[0]
            source_hash = learning.source_fingerprint(db,'dram')
            source_job = enqueue_task(db,'knowledge.publish_source',pid,'fixture',priority=30)
            payload = {'question_id':qid,'source_hash':source_hash,'input_hash':'material'}
            with patch('backend.knowledge_build.sync_demands', return_value=[source_job]) as demands:
                job = learning._prepare_gap_followup(db,question,payload,[pid],priority=30,origin_hash='origin')
                self.assertIsNotNone(job)
                self.assertEqual(demands.call_args.kwargs['question'],question['question'])
                self.assertIsNone(learning._prepare_gap_followup(db,question,payload,[pid],priority=30,origin_hash='origin'))
                self.assertIsNone(learning._prepare_gap_followup(db,question,{**payload,'after_source_preparation':True},[pid],priority=30,origin_hash='origin'))
            self.assertEqual(db.execute('SELECT depends_on FROM work_dependencies WHERE task_id=?',(job,)).fetchone()[0],source_job)

    def test_validated_answer_requires_review_and_does_not_close_user_error(self):
        with connect(self.db) as db:
            qid = self.question(db)
            fingerprint = learning.source_fingerprint(db, 'dram')
            pid = db.execute('SELECT id FROM papers').fetchone()[0]
            db.execute('INSERT INTO learning_gaps VALUES(?,?,?,?,?,?,?,?,?)',
                ('gap',qid,'dram','incorrect','Verify this finding','open','feedback','2026','2026'))
            db.commit()
        context = SimpleNamespace(database=self.db, check_cancelled=lambda:None)
        with patch('backend.answer_cache.prepare_case', return_value={'id':'case','status':'evidence_ready','investigation_complete':True}), \
             patch('backend.answer_cache.read_prepared_case', return_value={'bundle':{'diagnostics':{'paper_ids':[pid]}}}), \
             patch('backend.answer_cache.precompute_answer', return_value={'status':'answer_validated','validation_status':'model_reviewed'}) as answer, \
             patch('backend.knowledge_build.request_synthesis', return_value='synthesis') as synthesis:
            result = learning._evaluate({'question_id':qid,'source_hash':fingerprint,'input_hash':'material'}, context)
        self.assertEqual(result['status'],'answer_validated')
        self.assertEqual(answer.call_args.kwargs['owner_id'],bootstrap_admin_id())
        self.assertEqual(synthesis.call_args.kwargs['owner_type'],'learning')
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT state FROM learning_gaps WHERE id=\'gap\'').fetchone()[0],'open')
            self.assertEqual(db.execute('SELECT count(*) FROM claims').fetchone()[0],1)

    def test_sharing_revoked_during_preparation_cannot_publish_common_packet(self):
        from backend.task_queue import WorkCancelled
        request = {'id':'shared', 'visibility':'team', 'question':'How does refresh change?', 'expert_ids':['dram']}
        with connect(self.db) as db:
            learning.observe_research_request(db, request)
            qid = db.execute('SELECT id FROM learning_questions').fetchone()[0]
            fingerprint = learning.source_fingerprint(db,'dram')
            db.commit()
        def prepare(*args,**kwargs):
            with connect(self.db) as db:
                learning.observe_research_request(db,dict(request,visibility='private'))
                db.execute('INSERT INTO prepared_research_cases VALUES(?,?,?,?,?,?,?,?,?,?)',
                    ('race','',request['question'],scope_key(['dram']),'["dram"]','v','{}','[]','evidence_ready','2026'))
                db.commit()
            return {'id':'race','status':'needs_evidence'}
        with patch('backend.answer_cache.prepare_case',side_effect=prepare):
            with self.assertRaises(WorkCancelled):
                learning._evaluate({'question_id':qid,'source_hash':fingerprint,'input_hash':'material'},SimpleNamespace(database=self.db,check_cancelled=lambda:None))
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM prepared_research_cases').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT state FROM learning_questions').fetchone()[0],'inactive')

    def test_feedback_invalidates_exact_cache_when_result_is_a_new_cached_run(self):
        request = dict(id='req',owner_id=bootstrap_admin_id(),question='How does refresh change?',expert_ids=['dram'],
                       visibility='private',result={'run_id':'new-immutable-run'})
        with connect(self.db) as db:
            db.execute('''INSERT INTO research_requests(id,owner_id,question,expert_ids_json,expert_names_json,
                visibility,status,phase,input_hash,local_evidence_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                ('req',bootstrap_admin_id(),request['question'],'["dram"]','["DRAM"]','private','completed','completed','v','{}','2026','2026'))
            db.execute('INSERT INTO research_answer_cache VALUES(?,?,?,?,?,?)',
                       (bootstrap_admin_id(),request['question'],scope_key(['dram']),'v','original-cached-run','2026'))
            db.commit()
        with patch('backend.research_requests.read_request', return_value=request), \
             patch('backend.knowledge_learning.current_user', return_value={'employee_id':bootstrap_admin_id()}):
            learning.feedback(learning.Feedback(request_id='req',kind='incorrect'))
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM research_answer_cache').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM learning_questions').fetchone()[0],0)

    def test_guard_rejects_withdrawn_feedback_when_independent_plan_survives(self):
        from backend.task_queue import WorkCancelled
        request = dict(id='shared',visibility='team',question='How does refresh change?',expert_ids=['dram'])
        with connect(self.db) as db:
            learning.observe_research_request(db,request)
            qid = self.question(db)
            source_hash = learning.source_fingerprint(db,'dram')
            db.commit()
        def prepare(*args,**kwargs):
            with connect(self.db) as db:
                db.execute('BEGIN IMMEDIATE')
                learning.observe_research_request(db,dict(request,visibility='private'))
                self.assertTrue(learning._available(db,qid))
                self.assertFalse(kwargs['publication_guard'](db))
                db.commit()
            raise WorkCancelled('Sharing changed before publication')
        with patch('backend.answer_cache.prepare_case',side_effect=prepare):
            with self.assertRaises(WorkCancelled):
                learning._evaluate({'question_id':qid,'source_hash':source_hash,'input_hash':'material'},
                                   SimpleNamespace(database=self.db,check_cancelled=lambda:None))


if __name__ == '__main__':
    unittest.main()
