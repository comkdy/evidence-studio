"""Demand construction tests use only temporary PDFs/SQLite and fake models."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_pilot import make_pdf,TEXT
from backend.pilot_db import initialize,connect,now
from backend.pilot_ingest import ingest_file
from backend.wiki_retrieval import retrieve,refresh_local_index
from backend.wiki_store import WikiStore,WikiError
from backend import knowledge_build as build
from backend.task_queue import run_next,WorkCancelled
import test_pilot as pilot_fixtures
from backend.pilot_curate import apply_curation
from backend.chunking import ChunkPolicy,prepare_document_index,reindex_document
from backend.embedding_client import EmbeddingConfig,EmbeddingError,CachedEmbedder
from backend.ai_settings import ModelSettings
from pydantic import SecretStr


class FakeModel:
    configured=True
    calls=[]
    approve=True
    bad_quote=False
    database=None
    def __init__(self,*args,role='background',database=None,**kwargs):
        self.role=role;self.identity='fixture-'+role;self.service=SimpleNamespace(model=self.identity)
        self.database=database or type(self).database
    def complete_json(self,system,payload):
        # A different connection can obtain a write lock while the remote call
        # would be running. This catches accidentally held ingest/task locks.
        with connect(self.database) as db:
            db.execute('BEGIN IMMEDIATE');db.rollback()
        self.calls.append((self.role,payload))
        if self.role=='review':return {'approved':self.approve,'issues':[] if self.approve else ['조건이 원문으로 지지되지 않음']}
        evidence=payload['evidence'];key=next(iter(evidence));ev=evidence[key]
        item={'text':'제시된 구간의 retention 조건과 한계를 함께 확인합니다.','evidence_ids':[key]}
        if ev['kind']=='source_text':item['quotes']={key:'The fabricated quotation is absent.' if self.bad_quote else ev['text'][:80]}
        return {'sections':[{'heading':'선별 근거','statements':[item]}]}


class KnowledgeBuildTests(unittest.TestCase):
    curation=pilot_fixtures.PilotTests.curation
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'test.sqlite3'
        values={'EVIDENCE_DB_PATH':str(self.db),'AUTH_MODE':'local_admin','LLM_BASE_URL':'','LLM_API_KEY':'','LLM_MODEL':'','LLM_BASE_MODEL':'','LLM_LIGHT_MODEL':'',
                'EMBEDDING_BASE_URL':'','EMBEDDING_API_KEY':'','EMBEDDING_MODEL':'','EMBEDDING_ENDPOINT':'','RERANKER_BASE_URL':'','CHUNKING_MODE':'auto'}
        for role in ('BACKGROUND','ANSWER','REVIEW'):
            for field in ('BASE_URL','API_KEY','MODEL'):values[f'LLM_{role}_{field}']=''
        self.env=patch.dict(os.environ,values);self.env.start()
        self.model=patch('backend.knowledge_build.CompanyLLM',FakeModel);self.model.start()
        FakeModel.calls=[];FakeModel.approve=True;FakeModel.bad_quote=False;FakeModel.database=self.db
        initialize(self.db);build.register_handlers()
    def tearDown(self):
        self.model.stop();self.env.stop();self.tmp.cleanup()
    def ingest(self,text=TEXT,name='paper.pdf'):
        path=self.root/name;make_pdf(path,text)
        result=ingest_file(path,self.db,0)
        self.assertEqual(result['status'],'ready',result)
        return result['paper_id']
    def drain(self):
        for _ in range(300):
            if not run_next(self.db,kinds=['knowledge.'+n for n in ('extract','review','publish_source','expert_index','synthesize','review_synthesis','publish_synthesis')]):break
        else:self.fail('Unexpected unbounded work')
        with connect(self.db) as db:
            errors=[dict(r) for r in db.execute("SELECT * FROM work_tasks WHERE state='failed'")]
        self.assertEqual(errors,[])
    def test_raw_qa_fts_is_available_before_wiki_or_models(self):
        pid=self.ingest()
        with patch('backend.embedding_client.EmbeddingClient.embed',side_effect=AssertionError('no remote retrieval')):
            rows,_=retrieve('OCTRAM retention',['dram'],self.db,local_only=True)
        self.assertTrue(any(pid in json.loads(r['paper_ids_json']) and r['kind']=='passage' for r in rows))
        self.assertEqual(WikiStore(self.db).rows(),[])
        self.assertEqual(FakeModel.calls,[])
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT count(*) FROM wiki_jobs').fetchone()[0],0)

    def test_new_expert_member_refreshes_existing_specialization_without_rereading_old_source(self):
        from backend.wiki_engine import snapshot,current
        pid=self.ingest();self.drain()
        with connect(self.db) as db:
            build.request_synthesis(db,title='Retention limits',paper_ids=[pid],expert_id='dram',
                kind='specialization',owner_type='expert',owner_id='dram')
            db.commit()
        self.drain()
        store=WikiStore(self.db)
        note=next(r for r in store.rows() if r['kind']=='specialization')
        before=store.record('source-'+pid)['content_hash']
        calls=len(FakeModel.calls)
        self.ingest(TEXT+' Another DRAM experiment.',name='second.pdf')
        self.assertEqual(len(FakeModel.calls),calls)
        self.assertFalse(current(store.record(note['id']),snapshot(self.db),{r['id']:r for r in store.rows()}))
        with connect(self.db) as db:
            self.assertTrue(db.execute("SELECT 1 FROM work_tasks WHERE kind='knowledge.synthesize' AND target=? AND state='queued'",(note['id'],)).fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM wiki_jobs').fetchone()[0],0)
        self.drain()
        self.assertTrue(current(store.record(note['id']),snapshot(self.db),{r['id']:r for r in store.rows()}))
        self.assertEqual(store.record('source-'+pid)['content_hash'],before)
        self.assertFalse(any(payload.get('page_kind')=='source' and any(ev.get('paper_id')==pid for ev in payload['evidence'].values()) for role,payload in FakeModel.calls[calls:] if role=='background'))

    def test_bootstrap_existing_corpus_is_local_idempotent_and_preserves_manual_jobs(self):
        pid=self.ingest()
        with connect(self.db) as db:
            for table in ('work_dependencies','work_tasks','knowledge_demands','knowledge_extracts','retrieval_fts','retrieval_units'):db.execute('DELETE FROM '+table)
            reasons=['papers, evidence or expert settings changed','Wiki writing policy checked: 012345abcdef','manual reconciliation','administrator refreshed common Wiki','Wiki writing policy checked: requested by user']
            ids=[]
            for reason in reasons:ids.append(db.execute('INSERT INTO wiki_jobs(reason,created_at) VALUES(?,?)',(reason,now())).lastrowid)
            with patch.object(FakeModel,'complete_json',side_effect=AssertionError('bootstrap must be local')):
                result=build.bootstrap_local_state(db)
                count=db.execute('SELECT count(*) FROM work_tasks').fetchone()[0]
                self.assertEqual(build.bootstrap_local_state(db),{'status':'unchanged'})
                self.assertEqual(db.execute('SELECT count(*) FROM work_tasks').fetchone()[0],count)
            self.assertEqual(result['superseded_automatic_jobs'],ids[:2])
            self.assertEqual([r[0] for r in db.execute('SELECT state FROM wiki_jobs ORDER BY id')],['done','done','queued','queued','queued'])
            self.assertEqual(db.execute('SELECT count(*) FROM papers').fetchone()[0],1)
            db.commit()
        hits,_=retrieve('OCTRAM retention',['dram'],self.db,local_only=True)
        self.assertTrue(any(pid in json.loads(row['paper_ids_json']) for row in hits));self.assertEqual(FakeModel.calls,[])
        from backend.processing import processing_status
        self.assertTrue(any(row['stage']=='수요 기반 지식 작업으로 전환' for row in processing_status(self.db)['tasks']))

    def test_invalid_optional_model_settings_do_not_block_raw_ingestion(self):
        with patch('backend.knowledge_build.CompanyLLM',side_effect=ValueError('invalid optional model configuration')),patch.dict(os.environ,{'EMBEDDING_MODEL':'fixture','EMBEDDING_BASE_URL':'http://fixture.invalid','EMBEDDING_MAX_INPUT_TOKENS':'invalid'}):
            pid=self.ingest()
        hits,_=retrieve('OCTRAM retention',['dram'],self.db,local_only=True)
        self.assertTrue(any(pid in json.loads(row['paper_ids_json']) for row in hits))

    def test_new_model_stages_obey_current_context_budget_before_call(self):
        model=FakeModel(database=self.db)
        with patch('backend.wiki_budget.budget_for',return_value=SimpleNamespace(fits=lambda system,payload:False)),self.assertRaises(WikiError):
            build._complete(model,'policy',{'evidence':'large'})
        self.assertEqual(FakeModel.calls,[])
    def test_semantic_setting_never_calls_embedding_inside_ingest(self):
        with patch.dict(os.environ,{'CHUNKING_MODE':'semantic','EMBEDDING_BASE_URL':'http://fixture.invalid','EMBEDDING_MODEL':'fixture','EMBEDDING_API_KEY':'fixture'}),\
             patch('backend.embedding_client.EmbeddingClient.embed',side_effect=AssertionError('no model during ingest')):
            self.ingest()
        with connect(self.db) as db:
            policy=json.loads(db.execute('SELECT policy_json FROM search_index_state').fetchone()[0])
            self.assertNotEqual(policy.get('mode'),'semantic')
            self.assertTrue(db.execute("SELECT 1 FROM work_tasks WHERE kind='knowledge.embed'").fetchone())
    def test_experts_share_extraction_tasks_and_no_global_job(self):
        pid=self.ingest()
        with connect(self.db) as db:
            before=[tuple(r) for r in db.execute("SELECT id,input_hash FROM work_tasks WHERE kind='knowledge.extract'")]
            db.execute("INSERT INTO expert_categories VALUES('other','Other expert','retention','')")
            db.execute("INSERT INTO expert_papers VALUES('other',?,'test')",(pid,))
            build.sync_demands(db,expert_id='other');db.commit()
            after=[tuple(r) for r in db.execute("SELECT id,input_hash FROM work_tasks WHERE kind='knowledge.extract'")]
            self.assertEqual(before,after)
            self.assertEqual(db.execute('SELECT count(*) FROM wiki_jobs').fetchone()[0],0)
        self.drain()
        self.assertEqual(sum(role=='background' for role,_ in FakeModel.calls),len(before))
    def test_separate_review_precedes_publication_and_never_creates_claim(self):
        pid=self.ingest();self.drain()
        row=WikiStore(self.db).record('source-'+pid)
        self.assertIsNotNone(row);self.assertEqual(row['producer'],'demand-extraction-v1')
        refs=json.loads(row['refs_json'])
        self.assertEqual(refs['coverage']['scientific_review'],'human_pending')
        self.assertFalse(refs['coverage']['complete_paper_review'])
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM claims').fetchone()[0],0)
            self.assertTrue(db.execute("SELECT 1 FROM retrieval_units WHERE kind='wiki' AND reference_id=?",(row['id'],)).fetchone())
        self.assertEqual(sum(r=='background' for r,_ in FakeModel.calls),refs['coverage']['reviewed_chunks'])
        self.assertEqual(sum(r=='review' for r,_ in FakeModel.calls),refs['coverage']['reviewed_chunks'])
    def test_rejected_review_does_not_publish(self):
        pid=self.ingest();FakeModel.approve=False;self.drain()
        self.assertIsNone(WikiStore(self.db).record('source-'+pid))
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT state FROM knowledge_extracts').fetchone()[0],'rejected')
    def test_exact_quote_validation_rejects_before_model_review(self):
        self.ingest();FakeModel.bad_quote=True
        with connect(self.db) as db:payload=json.loads(db.execute("SELECT payload_json FROM work_tasks WHERE kind='knowledge.extract'").fetchone()[0])
        ctx=SimpleNamespace(database=self.db,job_id='test',check_cancelled=lambda:None,checkpoint=lambda _:None)
        with self.assertRaises(WikiError):build._extract(payload,ctx)
        self.assertEqual([r for r,_ in FakeModel.calls],['background'])
    def test_raw_change_prevents_review_and_publication(self):
        self.ingest()
        with connect(self.db) as db:payload=json.loads(db.execute("SELECT payload_json FROM work_tasks WHERE kind='knowledge.extract'").fetchone()[0])
        ctx=SimpleNamespace(database=self.db,job_id='test',check_cancelled=lambda:None,checkpoint=lambda _:None)
        build._extract(payload,ctx)
        with connect(self.db) as db:db.execute("UPDATE pages SET text='Changed source completely'");db.commit()
        self.assertEqual(build._review(payload,ctx)['status'],'obsolete_or_no_demand')
        self.assertEqual([r for r,_ in FakeModel.calls],['background'])
    def test_background_expands_beyond_initial_two_intervals_without_reread(self):
        pid=self.ingest(TEXT+'\n'+('\n'.join('Retention experiment condition number '+str(n)+' measured current at 90 C. '*12 for n in range(45))))
        self.drain()
        with connect(self.db) as db:
            initial=db.execute("SELECT count(*) FROM knowledge_extracts WHERE state='approved'").fetchone()[0]
            self.assertEqual(initial,2)
            total=build.coverage_status(db,paper_ids=[pid])['papers'][0]['total_chunks']
            self.assertGreater(total,2)
            self.assertTrue(build.enqueue_background_work(db));db.commit()
        self.drain()
        with connect(self.db) as db:
            count=db.execute("SELECT count(*) FROM knowledge_extracts WHERE state='approved'").fetchone()[0]
            self.assertEqual(count,4)
            self.assertEqual(build.coverage_status(db,paper_ids=[pid])['partial'],1)
        self.assertEqual(sum(role=='background' for role,_ in FakeModel.calls),4)
    def test_disabling_expert_releases_work_but_preserves_shared_wiki(self):
        pid=self.ingest();self.drain();old=WikiStore(self.db).record('source-'+pid)
        with connect(self.db) as db:
            db.execute("INSERT INTO expert_deletions VALUES('dram',?)",(now(),))
            build.sync_demands(db,expert_id='dram');db.commit()
        after=WikiStore(self.db).record('source-'+pid)
        self.assertEqual(old['content_hash'],after['content_hash']);self.assertEqual(after['status'],'ready')
    def test_topic_requires_review_and_shared_withdrawal_preserves_history(self):
        pid=self.ingest();self.drain()
        with connect(self.db) as db:
            first=build.request_synthesis(db,title='Retention 조건 비교',paper_ids=[pid],owner_type='shared',owner_id='one')
            second=build.request_synthesis(db,title='Retention 조건 비교',paper_ids=[pid],owner_type='shared',owner_id='two')
            self.assertEqual(first,second);db.commit()
        self.drain()
        with connect(self.db) as db:
            sid=db.execute('SELECT id FROM knowledge_syntheses').fetchone()[0]
            build.release_owner_demands(db,'shared','one',withdraw_shared=True);db.commit()
        self.assertEqual(WikiStore(self.db).record(sid)['status'],'ready')
        with connect(self.db) as db:
            build.release_owner_demands(db,'shared','two',withdraw_shared=True);db.commit()
            self.assertFalse(db.execute("SELECT 1 FROM retrieval_units WHERE reference_id=?",(sid,)).fetchone())
            self.assertTrue(db.execute('SELECT 1 FROM wiki_revisions WHERE page_id=?',(sid,)).fetchone())
        self.assertEqual(WikiStore(self.db).record(sid)['status'],'archived')
    def test_transaction_rollback_does_not_leave_demand_or_fts_changes(self):
        pid=self.ingest()
        with connect(self.db) as db:
            before=db.execute('SELECT count(*) FROM work_tasks').fetchone()[0]
            build.sync_demands(db,paper_ids=[pid],owner_type='research',owner_id='rolled-back')
            db.rollback()
            self.assertFalse(db.execute("SELECT 1 FROM knowledge_demands WHERE owner_id='rolled-back'").fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM work_tasks').fetchone()[0],before)

    def test_existing_claims_and_curated_wiki_do_not_hide_unanalyzed_source(self):
        self.pdf=self.root/'real.pdf'
        pid=self.ingest(name='real.pdf')
        target,_=self.curation();apply_curation(target,self.db)
        from backend.wiki_engine import snapshot,source_page
        state=snapshot(self.db);store=WikiStore(self.db)
        old=source_page(store,state,state['papers'][pid],None,'fixture canonical source')[-1]
        self.drain()
        self.assertEqual(store.record('source-'+pid)['content_hash'],old['content_hash'])
        supplement=store.record('source-evidence-'+pid)
        self.assertIsNotNone(supplement)
        self.assertGreater(sum(role=='background' for role,_ in FakeModel.calls),0)
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT count(*) FROM claims').fetchone()[0],1)

    def test_curation_after_raw_analysis_adds_canonical_source_and_keeps_raw_supplement(self):
        self.pdf=self.root/'real.pdf';pid=self.ingest(name='real.pdf');self.drain()
        original=WikiStore(self.db).record('source-'+pid)
        target,_=self.curation();apply_curation(target,self.db);self.drain()
        source=WikiStore(self.db).record('source-'+pid)
        self.assertEqual(source['producer'],'curated-db-migration')
        self.assertIn('claim1',json.loads(source['refs_json'])['claim_ids'])
        self.assertIsNotNone(WikiStore(self.db).record('source-evidence-'+pid))
        with connect(self.db) as db:self.assertTrue(db.execute('SELECT 1 FROM wiki_revisions WHERE page_id=? AND content_hash=?',(source['id'],original['content_hash'])).fetchone())

    def test_coverage_without_explicit_processed_intervals_is_partial(self):
        self.pdf=self.root/'real.pdf';pid=self.ingest(name='real.pdf')
        target,_=self.curation();apply_curation(target,self.db)
        from backend.wiki_engine import snapshot,source_page
        state=snapshot(self.db);source_page(WikiStore(self.db),state,state['papers'][pid],None,'fixture')
        with connect(self.db) as db:
            coverage=build.coverage_status(db,paper_ids=[pid])
            self.assertEqual(coverage['ready'],0);self.assertEqual(coverage['partial'],1)

    def test_withdrawal_between_precheck_and_publish_is_fenced(self):
        pid=self.ingest();self.drain()
        with connect(self.db) as db:
            build.request_synthesis(db,title='Publication race fixture',paper_ids=[pid],owner_type='shared',owner_id='race');db.commit()
        kinds=['knowledge.synthesize','knowledge.review_synthesis']
        while run_next(self.db,kinds=kinds):pass
        with connect(self.db) as db:payload=json.loads(db.execute("SELECT payload_json FROM work_tasks WHERE kind='knowledge.publish_synthesis'").fetchone()[0])
        ctx=SimpleNamespace(database=self.db,job_id='test',check_cancelled=lambda:None,checkpoint=lambda _:None)
        original=WikiStore.publish
        def withdraw_then_publish(store,*args,**kwargs):
            with connect(self.db) as db:build.release_owner_demands(db,'shared','race',withdraw_shared=True);db.commit()
            return original(store,*args,**kwargs)
        with patch.object(WikiStore,'publish',withdraw_then_publish):
            with self.assertRaises(WorkCancelled):build._publish_synthesis(payload,ctx)
        self.assertIsNone(WikiStore(self.db).record(payload['artifact_id']))

    def test_semantic_prepare_has_no_corpus_write_lock_and_publishes_atomically(self):
        self.ingest()
        policy=ChunkPolicy(mode='semantic',embedding=EmbeddingConfig(ModelSettings('http://fixture.invalid','fixture',SecretStr('fixture'))))
        database=self.db
        class Vectors:
            def __init__(self,*args,**kwargs):pass
            def embed(self,texts):
                with connect(database) as db:db.execute('BEGIN IMMEDIATE');db.rollback()
                return [[1.,0.] for _ in texts]
        with connect(self.db) as db:
            run=db.execute('SELECT current_run_id FROM paper_documents').fetchone()[0]
            before=[tuple(r) for r in db.execute('SELECT * FROM search_chunks')]
        with patch('backend.wiki_retrieval.GatedEmbeddingClient',Vectors),connect(self.db) as db:prepared=prepare_document_index(db,run,policy)
        with connect(self.db) as db:
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM search_chunks')])
            self.assertEqual(db.execute('SELECT count(*) FROM chunk_embedding_cache').fetchone()[0],0)
            db.execute('BEGIN IMMEDIATE');result=reindex_document(db,run,policy,prepared=prepared);db.commit()
            self.assertEqual(result['status'],'indexed')
            self.assertGreater(db.execute('SELECT count(*) FROM chunk_embedding_cache').fetchone()[0],0)
        with patch('backend.wiki_retrieval.GatedEmbeddingClient',side_effect=AssertionError('unchanged should not call a model')):
            with connect(self.db) as db:self.assertIsNone(prepare_document_index(db,run,policy)['groups'])

    def test_semantic_publish_rejects_changed_source_without_index_writes(self):
        self.ingest()
        with connect(self.db) as db:
            run=db.execute('SELECT current_run_id FROM paper_documents').fetchone()[0]
            before=[tuple(r) for r in db.execute('SELECT * FROM search_chunks')]
        with connect(self.db) as db:prepared=prepare_document_index(db,run,ChunkPolicy(max_tokens=256,overlap_tokens=32))
        with connect(self.db) as db:
            db.execute("UPDATE papers SET title='Changed while preparing'");db.commit()
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(ValueError):reindex_document(db,run,prepared['policy'],prepared=prepared)
            db.rollback()
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM search_chunks')])

    def test_query_embedding_gate_is_only_entered_for_cache_miss(self):
        from backend.wiki_retrieval import GatedEmbeddingClient
        config=EmbeddingConfig(ModelSettings('http://fixture.invalid','fixture',SecretStr('fixture')))
        self.ingest()
        with patch('backend.embedding_client.EmbeddingClient.embed',return_value=[[1.,0.]]) as remote:
            with connect(self.db) as db:
                CachedEmbedder(db,config,client=GatedEmbeddingClient(config,self.db,priority=0)).embed(['fixture query']);db.commit()
                CachedEmbedder(db,config,client=GatedEmbeddingClient(config,self.db,priority=0)).embed(['fixture query']);db.commit()
                self.assertEqual(db.execute("SELECT count(*) FROM model_call_metrics WHERE role='embedding'").fetchone()[0],1)
        self.assertEqual(remote.call_count,1)

    def test_embedding_metrics_failure_does_not_repeat_successful_model_request(self):
        from backend.wiki_retrieval import GatedEmbeddingClient
        config=EmbeddingConfig(ModelSettings('http://fixture.invalid','fixture',SecretStr('fixture')))
        with patch('backend.embedding_client.EmbeddingClient.embed',return_value=[[1.,0.]]) as remote,patch('backend.task_queue.record_model_call',side_effect=OSError('fixture telemetry contention')):
            self.assertEqual(GatedEmbeddingClient(config,self.db).embed(['fixture']),[[1.,0.]])
        self.assertEqual(remote.call_count,1)

    def test_dependent_queued_task_does_not_hide_failed_source_stage(self):
        self.ingest()
        with connect(self.db) as db:
            row=db.execute("SELECT id FROM work_tasks WHERE kind='knowledge.extract' LIMIT 1").fetchone()
            db.execute("UPDATE work_tasks SET state='failed',error='fixture failure' WHERE id=?",(row['id'],));db.commit()
        from backend.processing import processing_status
        status=processing_status(self.db)
        self.assertGreater(status['counts']['failed'],0)
        self.assertTrue(any(row['state']=='failed' and row['kind']=='knowledge' for row in status['tasks']))

    def test_false_numbers_units_and_conditions_cannot_be_saved_or_published(self):
        pid=self.ingest();self.drain()
        with connect(self.db) as db:
            artifact=dict(db.execute('SELECT * FROM knowledge_extracts LIMIT 1').fetchone())
            payload=json.loads(db.execute("SELECT payload_json FROM work_tasks WHERE kind='knowledge.publish_source' ORDER BY created_at DESC LIMIT 1").fetchone()[0])
        evidence={artifact['id']:json.loads(artifact['evidence_json'])}
        key=artifact['id'];quote=evidence[key]['text']
        for text in ('Retention exceeded 999 seconds.','Retention exceeded 100 seconds at room temperature.'):
            reply={'sections':[{'heading':'Unchecked conclusion 999','statements':[{'text':text,'evidence_ids':[key],'quotes':{key:quote},'kind':'observed'}]}]}
            with self.subTest(text=text),self.assertRaises(WikiError):build._validate_candidate(reply,evidence)
        ev=dict(next(iter(evidence.values())),text='Measured drain current was 100 mA at 90 C under the specified bias.')
        reply={'sections':[{'heading':'Current','statements':[{'text':'Measured current was 100 MA.','evidence_ids':[key],'quotes':{key:ev['text']},'kind':'observed'}]}]}
        with self.assertRaises(WikiError):build._validate_candidate(reply,{key:ev})
        # Even a persisted approving review cannot bypass the publication guard.
        sections=json.loads(artifact['sections_json']);sections[0]['statements'][0]['text']='Retention exceeded 999 seconds.'
        with connect(self.db) as db:
            db.execute("UPDATE knowledge_extracts SET sections_json=?,review_json=? WHERE id=?",(json.dumps(sections),'{"approved":true,"issues":[]}',key));db.commit()
        ctx=SimpleNamespace(database=self.db,job_id='test',check_cancelled=lambda:None,checkpoint=lambda _:None)
        old=WikiStore(self.db).record('source-'+pid)
        with self.assertRaises(WikiError):build._publish_source(payload,ctx)
        self.assertEqual(WikiStore(self.db).record('source-'+pid)['content_hash'],old['content_hash'])

    def test_synthesis_syntax_uses_cited_primary_sources_and_preserves_kinds(self):
        ev={'source':dict(kind='wiki',text='An unchecked Wiki statement mentions 999.',primary_sources=['Measured drain current was 100 mA at 90 C.'])}
        for text in ('Measured current was 100 MA.','Measured current was 999 mA.','Measured current was 100 mA at room temperature.'):
            reply={'sections':[{'heading':'Evidence','statements':[{'text':text,'evidence_ids':['source']}]}]}
            with self.subTest(text=text),self.assertRaises(WikiError):build._validate_candidate(reply,ev)
        reply={'sections':[{'heading':'An uncited conclusion','statements':[{'text':'Measured current was 100 mA at 90 C.','evidence_ids':['source'],'kind':'estimated'}]}]}
        checked=build._validate_candidate(reply,ev)
        self.assertEqual(checked[0]['statements'][0]['kind'],'estimated');self.assertEqual(checked[0]['heading'],'근거와 해석')
        del reply['sections'][0]['statements'][0]['kind']
        self.assertEqual(build._validate_candidate(reply,ev)[0]['statements'][0]['kind'],'unclassified')

    def test_stale_canonical_quote_cannot_cover_current_raw_interval(self):
        self.pdf=self.root/'real.pdf';pid=self.ingest(name='real.pdf')
        target,_=self.curation();apply_curation(target,self.db)
        with connect(self.db) as db:
            unit=db.execute("SELECT * FROM retrieval_units WHERE kind='passage' LIMIT 1").fetchone();ev=build._evidence(unit)
            db.execute('UPDATE evidence_spans SET start_offset=?,end_offset=?,quote=?',(ev['offset'],ev['offset']+len(ev['text']),'stale text'))
            self.assertFalse(build._canonical_covers(db,unit));db.rollback()

    def test_policy_change_invalidates_approved_coverage_without_deleting_history(self):
        pid=self.ingest();self.drain();old=WikiStore(self.db).record('source-'+pid)
        with patch.object(build,'VALIDATOR_VERSION','future-validation'):
            from backend.wiki_engine import current,snapshot
            self.assertFalse(current(old,snapshot(self.db),{old['id']:old}))
            with connect(self.db) as db:
                self.assertEqual(build.coverage_status(db,paper_ids=[pid])['papers'][0]['ready_chunks'],0)
                build.sync_demands(db,paper_ids=[pid],owner_type='shared',owner_id='new-policy');db.commit()
                self.assertEqual(db.execute("SELECT count(*) FROM knowledge_extracts WHERE state='approved'").fetchone()[0],0)
        self.assertEqual(WikiStore(self.db).record('source-'+pid)['content_hash'],old['content_hash'])

    def test_source_candidate_change_between_check_and_publish_is_fenced(self):
        pid=self.ingest();self.drain();old=WikiStore(self.db).record('source-'+pid)
        with connect(self.db) as db:
            payload=json.loads(db.execute("SELECT payload_json FROM work_tasks WHERE kind='knowledge.publish_source' ORDER BY created_at DESC LIMIT 1").fetchone()[0])
            # Make a valid new candidate version so the store needs to publish.
            db.execute("UPDATE knowledge_extracts SET review_json=?",('{"approved":true,"issues":[],"fixture":"new revision"}',));db.commit()
        ctx=SimpleNamespace(database=self.db,job_id='test',check_cancelled=lambda:None,checkpoint=lambda _:None)
        original=WikiStore.publish
        def reject_then_publish(store,*args,**kwargs):
            with connect(self.db) as db:db.execute("UPDATE knowledge_extracts SET state='rejected'");db.commit()
            return original(store,*args,**kwargs)
        with patch.object(WikiStore,'publish',reject_then_publish),self.assertRaises(WorkCancelled):build._publish_source(payload,ctx)
        self.assertEqual(WikiStore(self.db).record('source-'+pid)['content_hash'],old['content_hash'])

    def test_legacy_jobs_are_bridged_without_running_in_scheduler(self):
        from backend.wiki_engine import enqueue,enqueue_legacy_jobs,register_legacy_handler
        pid=self.ingest()
        with connect(self.db) as db:
            legacy=enqueue(db,'explicit common maintenance',scope='common');db.commit()
            with patch('backend.wiki_engine.reconcile',side_effect=AssertionError('scheduler must not run model work')):
                ids=enqueue_legacy_jobs(db);self.assertEqual(ids,enqueue_legacy_jobs(db));db.commit()
            task=db.execute('SELECT * FROM work_tasks WHERE id=?',(ids[0],)).fetchone()
            self.assertEqual(task['priority'],50);self.assertEqual(task['kind'],'wiki.legacy_reconcile')
        register_legacy_handler()
        with patch('backend.wiki_engine.reconcile',return_value={'status':'ready'}) as run:
            self.assertTrue(run_next(self.db,kinds=['wiki.legacy_reconcile']))
        self.assertEqual(run.call_count,1)
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT state FROM wiki_jobs WHERE id=?',(legacy,)).fetchone()[0],'done')

    def test_legacy_reconcile_uses_background_role_and_correct_database(self):
        from backend.wiki_engine import reconcile
        model=SimpleNamespace(configured=False)
        with patch('backend.wiki_engine.CompanyLLM',return_value=model) as constructor:
            reconcile(self.db)
        constructor.assert_called_once_with(role='background',database=self.db)

    def test_legacy_bridge_retries_deferred_work_and_preserves_service_cooldown(self):
        from backend.wiki_engine import enqueue,enqueue_legacy_jobs,register_legacy_handler
        import time
        with connect(self.db) as db:
            legacy=enqueue(db,'manual retry fixture',scope='common');first=enqueue_legacy_jobs(db)[0];db.commit()
        register_legacy_handler()
        with patch('backend.wiki_engine.run_jobs',return_value={'status':'deferred'}):self.assertTrue(run_next(self.db,kinds=['wiki.legacy_reconcile']))
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT state FROM work_tasks WHERE id=?',(first,)).fetchone()[0],'cancelled')
            self.assertEqual(db.execute('SELECT state FROM wiki_jobs WHERE id=?',(legacy,)).fetchone()[0],'queued')
            self.assertEqual(enqueue_legacy_jobs(db),[first]);db.commit()
        with patch('backend.wiki_engine.run_jobs',return_value={'status':'failed','error':'fixture slow service','retry_after_seconds':60}):self.assertTrue(run_next(self.db,kinds=['wiki.legacy_reconcile']))
        with connect(self.db) as db:
            enqueue_legacy_jobs(db);db.commit()
            self.assertGreater(db.execute('SELECT available_at FROM work_tasks WHERE id=?',(first,)).fetchone()[0],time.time()+50)

    def test_review_prompt_change_invalidates_approved_source_and_synthesis_signature(self):
        pid=self.ingest();self.drain();prompt=build.load_prompt;before=build._compiler_signature()
        def changed(name):
            value=prompt(name)
            return SimpleNamespace(fingerprint='changed-review-policy',text=value.text) if name=='lint' else value
        with patch.object(build,'load_prompt',changed):
            self.assertNotEqual(before,build._compiler_signature())
            with connect(self.db) as db:self.assertEqual(build.coverage_status(db,paper_ids=[pid])['papers'][0]['ready_chunks'],0)

    def test_obsolete_pending_rows_do_not_prevent_derived_refresh(self):
        pid=self.ingest();self.drain()
        with connect(self.db) as db:
            row=dict(db.execute('SELECT * FROM knowledge_extracts LIMIT 1').fetchone())
            db.execute("INSERT INTO knowledge_extracts VALUES(?,?,?,?,?,?,'{}','pending',?,?)",('old-extract',pid,'obsolete-unit','old-policy',row['evidence_json'],'[]',now(),now()))
            payload=json.loads(db.execute("SELECT payload_json FROM work_tasks WHERE kind='knowledge.publish_source' ORDER BY created_at DESC LIMIT 1").fetchone()[0]);db.commit()
        ctx=SimpleNamespace(database=self.db,job_id='test',check_cancelled=lambda:None,checkpoint=lambda _:None)
        with patch.object(build,'refresh_derived_for_papers',return_value=[]) as refresh:
            self.assertEqual(build._publish_source(payload,ctx)['status'],'published')
        self.assertEqual(refresh.call_count,1);self.assertEqual(refresh.call_args.args[1],[pid])

    def test_changed_document_identity_rebinds_unchanged_evidence_without_model_calls(self):
        pid=self.ingest();self.drain()
        with connect(self.db) as db:
            before={r['unit_id'] for r in db.execute('SELECT * FROM knowledge_extracts')}
            expected=build.coverage_status(db,paper_ids=[pid])['papers'][0]['ready_chunks']
            db.execute("UPDATE papers SET title='Corrected document title'")
            run=db.execute('SELECT current_run_id FROM paper_documents').fetchone()[0]
            reindex_document(db,run,ChunkPolicy());refresh_local_index(db,[pid]);db.commit()
            after={r['unit_id'] for r in db.execute('SELECT * FROM knowledge_extracts')}
            self.assertNotEqual(before,after)
            self.assertEqual(build.coverage_status(db,paper_ids=[pid])['papers'][0]['ready_chunks'],expected)
            self.assertEqual(build._selected_units(db,pid,remaining_only=True),[])
        self.assertEqual(len(FakeModel.calls),expected*2)

    def test_synthesis_identity_preserves_scientific_prefix_case(self):
        pid=self.ingest()
        with connect(self.db) as db:
            a=build.request_synthesis(db,title='Compare 1 mA',paper_ids=[pid],owner_type='shared',owner_id='case')
            b=build.request_synthesis(db,title='Compare 1 MA',paper_ids=[pid],owner_type='shared',owner_id='case')
            self.assertNotEqual(a,b);self.assertEqual(db.execute('SELECT count(*) FROM knowledge_syntheses').fetchone()[0],2)

    def test_explicit_reuse_review_promotes_once_and_sharing_withdrawal_hides_both(self):
        pid=self.ingest();self.drain();original=FakeModel.complete_json
        def reusable(model,system,payload):
            reply=original(model,system,payload)
            if model.role=='review' and payload.get('page_kind')=='specialization':reply.update(common_reuse_approved=True,common_title='Shared retention conditions')
            return reply
        with patch.object(FakeModel,'complete_json',reusable):
            with connect(self.db) as db:
                build.request_synthesis(db,title='Expert retention conditions',paper_ids=[pid],expert_id='dram',kind='specialization',owner_type='manual_shared',owner_id='origin');db.commit()
            self.drain()
        with connect(self.db) as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM knowledge_syntheses')]
            self.assertEqual(len(rows),2);self.assertEqual({r['kind'] for r in rows},{'topic','specialization'})
            self.assertTrue(all(r['state']=='published' for r in rows))
            build.release_owner_demands(db,'manual_shared','origin',withdraw_shared=True);db.commit()
        for row in rows:self.assertEqual(WikiStore(self.db).record(row['id'])['status'],'archived')


if __name__=='__main__':unittest.main()
