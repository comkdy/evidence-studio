import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch
from pydantic import SecretStr
from fastapi import HTTPException
import test_pilot as fixtures
from test_pilot import make_pdf,TEXT
from backend.pilot_db import connect,sync_wiki_memberships,now
from backend.pilot_curate import apply_curation
from backend.pilot_ingest import ingest_file
from backend.wiki_engine import reconcile,run_jobs,enqueue,snapshot,validate_sections
from backend.wiki_store import WikiStore,WikiError
from backend.wiki_lint import run_lint,adopt
from backend.wiki_api import Question,answer,list_wikis,detail,request_topic,TopicRequest
from backend.wiki_retrieval import retrieve,rerank
from backend.llm_client import CompanyLLM,LLMError
from backend.ai_settings import ModelSettings
from backend.wiki_policy import load_prompt, POLICY_PATH
from backend.task_queue import run_next
from backend.knowledge_build import register_handlers
from test_knowledge_build import FakeModel as ReviewedBuildModel


class FakeLLM:
    configured=True
    identity='test-model-v1'
    service=SimpleNamespace(model='test-model')
    def __init__(self):self.calls=[];self.systems=[]
    def complete_json(self,system,payload):
        self.calls.append(payload)
        self.systems.append(system)
        if 'statements' in payload:
            return {'checks':[{'id':item['id'],'verdict':'supported'} for item in payload['statements']]}
        if 'queries' in payload:
            return {'queries':[],'gaps':[]}
        if '검토자' in system:
            return {'findings':[]}
        if 'experts' in payload and 'question' in payload:
            if 'evidence' not in payload:
                return {'queries':[],'gaps':[]}
            evidence=payload['evidence']
            return {'synthesis':self.sections(evidence),'answers':[{'expert_id':p['expert_id'],**self.sections({key:evidence[key] for key in p['evidence_ids']})} for p in payload['experts']]}
        return self.sections(payload['evidence'])
    def sections(self,evidence):
        if not evidence:return {'sections':[]}
        key=next((key for key,ev in evidence.items() if ev['kind']!='wiki'),next(iter(evidence)));ev=evidence[key]
        quote={key:ev['text'][:80]} if ev['kind']=='source_text' else {}
        return {'sections':[{'heading':'확인된 지식','statements':[{'text':'근거에 제시된 결과와 적용 조건을 함께 확인합니다.','evidence_ids':[key],'quotes':quote}]}]}


class MarkdownWikiTests(unittest.TestCase):
    curation=fixtures.PilotTests.curation
    def setUp(self):
        fixtures.PilotTests.setUp(self)
        self.env=patch.dict(os.environ,{'EVIDENCE_DB_PATH':str(self.db),'EVIDENCE_USER_ROLE':'admin','AUTH_MODE':'local_admin','LLM_BASE_URL':'','LLM_API_KEY':'','LLM_BASE_MODEL':'','EMBEDDING_BASE_URL':'','EMBEDDING_API_KEY':'','EMBEDDING_MODEL':'','RERANKER_BASE_URL':''})
        self.env.start()
        target,_=self.curation();apply_curation(target,self.db)
        self.store=WikiStore(self.db)
        self.no_llm=CompanyLLM(ModelSettings('','',SecretStr('')))
    def tearDown(self):
        self.env.stop();fixtures.PilotTests.tearDown(self)
    def sync(self,llm=None):return reconcile(self.db,llm or self.no_llm)
    def source(self):return next(r for r in self.store.rows() if r['kind']=='source')
    def enable_reviewed_model(self):
        ReviewedBuildModel.database=self.db;ReviewedBuildModel.approve=True;ReviewedBuildModel.bad_quote=False
        model=patch('backend.knowledge_build.CompanyLLM',ReviewedBuildModel)
        model.start();self.addCleanup(model.stop)
    def drain_demand_work(self):
        register_handlers()
        for _ in range(150):
            if not run_next(self.db,kinds=['knowledge.'+name for name in ('extract','review','publish_source','expert_index','synthesize','review_synthesis','publish_synthesis')]):break
        else:self.fail('Unexpected unbounded demand work')
        with connect(self.db) as db:self.assertEqual(db.execute("SELECT count(*) FROM work_tasks WHERE state='failed'").fetchone()[0],0)

    def test_migration_keeps_canonical_evidence_and_is_idempotent(self):
        self.sync();before={r['id']:r['content_hash'] for r in self.store.rows()}
        source=self.source();meta,body=self.store.read(source)
        self.assertIn('Power reduction',body);self.assertIn('74%',body);self.assertIn('미기재',body)
        self.assertIn('claim1',meta['refs']['claim_ids'])
        self.assertTrue((self.store.root/'experts/dram/index.md').is_file())
        self.sync();self.assertEqual(before,{r['id']:r['content_hash'] for r in self.store.rows()})
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM claims').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM measurements').fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_new_expert_reuses_common_wiki_without_llm_reanalysis(self):
        llm=FakeLLM();self.sync(llm);llm.calls.clear()
        with connect(self.db) as db:
            pid=db.execute('SELECT id FROM papers').fetchone()[0]
            db.execute("INSERT INTO expert_categories VALUES('new','New expert','Focused expertise','')")
            db.execute("INSERT INTO expert_papers VALUES('new',?,'manual')",(pid,));sync_wiki_memberships(db);db.commit()
        self.sync(llm)
        self.assertEqual(llm.calls,[])
        self.assertTrue((self.store.root/'experts/new/index.md').is_file())
        self.assertFalse(any(r['expert_id']=='new' and r['kind']=='topic' for r in self.store.rows()))

    def test_requested_specialization_is_scoped_and_not_a_copy_for_every_expert(self):
        llm=FakeLLM();self.sync(llm)
        source=self.source()
        self.enable_reviewed_model()
        result=request_topic('dram',TopicRequest(title='Refresh trade-offs',source_ids=[source['id']]))
        self.assertEqual(result['status'],'queued');self.drain_demand_work()
        self.assertEqual(self.store.record(result['id'])['kind'],'specialization')
        with self.assertRaises(HTTPException):request_topic('nor',TopicRequest(title='Forbidden scope',source_ids=[source['id']]))

    def test_new_pdf_enqueues_update_and_adds_grounded_source_wiki(self):
        self.sync();pdf=self.root/'second.pdf';make_pdf(pdf,TEXT+' A second DRAM result was measured at 95 C.')
        self.enable_reviewed_model()
        result=ingest_file(pdf,self.db,0)
        self.assertEqual(result['status'],'ready');self.drain_demand_work()
        sources=[r for r in self.store.rows() if r['kind']=='source']
        self.assertEqual(len({pid for row in sources for pid in json.loads(row['refs_json'])['paper_ids']}),2)
        added=next(r for r in sources if result['paper_id'] in json.loads(r['refs_json'])['paper_ids'])
        spans=json.loads(added['refs_json'])['source_spans'];self.assertTrue(spans)
        state=snapshot(self.db)
        for span in spans:
            page=next(p for p in state['papers'][span['paper_id']]['pages'] if p['id']==span['page_id'])
            self.assertEqual(page['text'][span['start_offset']:span['end_offset']],span['quote'])

    def test_bad_llm_citation_does_not_overwrite_existing_page(self):
        self.sync();before={r['id']:r['content_hash'] for r in self.store.rows()}
        llm=FakeLLM();llm.complete_json=lambda *a:{'sections':[{'heading':'X','statements':[{'text':'Unfounded','evidence_ids':['invented']}]}]}
        with self.assertRaises(WikiError):self.sync(llm)
        self.assertEqual(before,{r['id']:r['content_hash'] for r in self.store.rows()})

    def test_scoped_refresh_preserves_other_wikis_and_their_search_units(self):
        llm=FakeLLM();self.sync(llm)
        source=self.source();pid=json.loads(source['refs_json'])['paper_ids'][0]
        with connect(self.db) as db:
            db.execute("INSERT INTO expert_papers VALUES('nor',?,'manual')",(pid,));db.commit()
        self.sync(llm)
        # This test explicitly covers legacy scoped maintenance. New topic
        # requests use the reviewed demand queue, tested separately above.
        with connect(self.db) as db:
            for eid in ('dram','nor'):
                db.execute('INSERT INTO wiki_topic_requests VALUES(?,?,?,?,?)',
                           ('legacy-'+eid,eid,eid+' focused note',json.dumps([source['id']]),now()))
            db.commit()
        self.sync(llm)
        initial={r['id']:r for r in self.store.rows()}
        llm.calls.clear();reconcile(self.db,llm,scope='expert',expert_id='dram')
        self.assertEqual(initial,{r['id']:r for r in self.store.rows()})
        self.assertEqual(llm.calls,[])
        def records(predicate):
            return {r['id']:(r,(self.store.root/r['path']).read_bytes()) for r in self.store.rows() if predicate(r)}
        def units(clause):
            with connect(self.db) as db:
                return [tuple(r) for r in db.execute('SELECT * FROM retrieval_units WHERE '+clause+' ORDER BY id')]
        expert_before=records(lambda r:r['scope']=='expert')
        expert_units=units('expert_id IS NOT NULL')
        with connect(self.db) as db:
            db.execute("UPDATE claims SET summary=summary || ' Updated evidence.' WHERE id='claim1'");db.commit()
        llm.calls.clear()
        reconcile(self.db,llm,scope='common')
        self.assertTrue(llm.calls)
        self.assertTrue(all(c['page_kind']!='specialization' for c in llm.calls))
        self.assertEqual(expert_before,records(lambda r:r['scope']=='expert'))
        self.assertEqual(expert_units,units('expert_id IS NOT NULL'))
        outside_before=records(lambda r:r['expert_id']!='dram')
        outside_units=units("expert_id IS NULL OR expert_id<>'dram'")
        llm.calls.clear()
        with patch('backend.wiki_engine.source_page',side_effect=AssertionError('must reuse common sources')):
            reconcile(self.db,llm,scope='expert',expert_id='dram')
        self.assertTrue(llm.calls)
        self.assertTrue(all(c['page_kind']=='specialization' for c in llm.calls))
        self.assertEqual(outside_before,records(lambda r:r['expert_id']!='dram'))
        self.assertEqual(outside_units,units("expert_id IS NULL OR expert_id<>'dram'"))
        self.assertGreater(self.store.record('expert-index-dram')['revision'],expert_before['expert-index-dram'][0]['revision'])
        llm.calls.clear();reconcile(self.db,llm,scope='expert',expert_id='dram')
        self.assertEqual(llm.calls,[])

    def test_expert_refresh_requires_current_common_sources(self):
        self.sync()
        before={r['id']:r for r in self.store.rows()}
        with connect(self.db) as db:
            db.execute("UPDATE claims SET summary=summary || ' Changed.' WHERE id='claim1'");db.commit()
        with self.assertRaisesRegex(WikiError,'공통 Wiki를 먼저'):
            reconcile(self.db,self.no_llm,scope='expert',expert_id='dram')
        self.assertEqual(before,{r['id']:r for r in self.store.rows()})

    def test_scoped_jobs_are_deduplicated_independently_and_failure_is_isolated(self):
        with connect(self.db) as db:
            db.execute('DELETE FROM wiki_jobs')
            first=enqueue(db,scope='expert',expert_id='deleted')
            common=enqueue(db,scope='common')
            self.assertEqual(common,enqueue(db,scope='common'))
            last=enqueue(db,scope='expert',expert_id='dram');db.commit()
        def process(*args,scope,expert_id):
            if expert_id=='deleted':raise WikiError('삭제된 전문가')
            return {'scope':scope,'expert_id':expert_id}
        with patch('backend.wiki_engine.reconcile',side_effect=process) as runner:
            self.assertEqual(run_jobs(self.db,self.no_llm)['status'],'failed')
        self.assertEqual([(c.kwargs['scope'],c.kwargs['expert_id']) for c in runner.call_args_list],
                         [('expert','deleted'),('common',''),('expert','dram')])
        with connect(self.db) as db:
            states=dict(db.execute('SELECT id,state FROM wiki_jobs'))
        self.assertEqual(states,{first:'failed',common:'done',last:'done'})

    def test_legacy_job_migration_preserves_pending_work(self):
        import sqlite3
        from contextlib import closing
        from backend.pilot_db import initialize
        legacy=self.root/'legacy.sqlite3'
        with closing(sqlite3.connect(legacy)) as db:
            db.execute("CREATE TABLE wiki_jobs(id INTEGER PRIMARY KEY,reason TEXT NOT NULL,created_at TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',finished_at TEXT,error TEXT NOT NULL DEFAULT '')")
            db.execute("INSERT INTO wiki_jobs(reason,created_at) VALUES('legacy pending work',?)",(now(),))
            db.commit()
        initialize(legacy);initialize(legacy)
        with connect(legacy) as db:
            row=db.execute('SELECT * FROM wiki_jobs').fetchone()
            self.assertEqual((row['reason'],row['state'],row['scope'],row['expert_id']),('legacy pending work','queued','all',''))

    def test_markdown_edit_is_protected_and_adopt_updates_dependents(self):
        self.sync();row=self.source();path=self.store.root/row['path']
        original=path.read_text(encoding='utf-8');path.write_text(original+'\n검색 별칭: retentionalias\n',encoding='utf-8')
        with self.assertRaises(WikiError):self.sync()
        result=adopt(row['id'],self.db);self.assertEqual(result['revision'],2)
        self.sync();self.assertIn('retentionalias',self.store.read(self.store.record(row['id']))[1])
        self.assertTrue(list_wikis(q='retentionalias')['wikis'])
        self.assertTrue((self.store.root/'log.md').read_text(encoding='utf-8').count('v2'))

    def test_scope_changes_hide_old_wiki_immediately_before_worker_runs(self):
        self.sync()
        with connect(self.db) as db:
            db.execute("DELETE FROM expert_papers WHERE expert_id='dram'");sync_wiki_memberships(db);db.commit()
        self.assertEqual(list_wikis(expert='dram')['total'],0)
        with self.assertRaises(HTTPException):detail('expert-index-dram')
        result=answer(Question(question='refresh 74',expert_ids=['dram']),self.no_llm)
        self.assertEqual(result['claims'],[]);self.assertEqual(result['hits'],[])

    def test_keyword_query_links_claim_measurements_and_raw_fallback(self):
        self.sync()
        result=answer(Question(question='refresh 74',expert_ids=['dram']),self.no_llm)
        self.assertEqual(result['claims'][0]['measurements'][0]['raw_value'],'74%')
        # Text present in a paper but absent from curated claims remains searchable.
        result=answer(Question(question='OCTRAM 90',expert_ids=['dram']),self.no_llm)
        self.assertTrue(result['hits'])

    def test_multiple_experts_use_one_answer_call_and_independent_evidence(self):
        self.sync();llm=FakeLLM()
        result=answer(Question(question='refresh 74',expert_ids=['dram','nor']),llm)
        answer_calls=[p for p in llm.calls if 'evidence' in p and 'experts' in p and 'queries' not in p]
        self.assertEqual(len(answer_calls),1)
        self.assertEqual(result['answers'][0]['mode'],'llm')
        no_scope=next(e for e in answer_calls[0]['experts'] if e['expert_id']=='nor')
        self.assertEqual(no_scope['evidence_ids'],[])

    def test_lint_is_budgeted_incremental_and_scheduled(self):
        self.sync(FakeLLM());llm=FakeLLM()
        first=run_lint(self.db,llm=llm)
        self.assertEqual(first['findings'],0);self.assertGreater(first['llm_calls'],0)
        llm.calls.clear();second=run_lint(self.db,llm=llm)
        self.assertEqual(second['llm_calls'],0)
        self.assertEqual(run_lint(self.db,llm=llm,due_only=True)['status'],'not_due')
        with patch('backend.wiki_lint.MAX_LLM_CALLS',1):
            llm.identity='new-test-model'
            bounded=run_lint(self.db,llm=llm)
        self.assertEqual(bounded['llm_calls'],1);self.assertLessEqual(bounded['review_chars'],60000)

    def test_lint_typography_is_anchored_to_body_and_bad_quote_is_reported(self):
        self.sync(FakeLLM())
        files={r['path']:(self.store.root/r['path']).read_bytes() for r in self.store.rows()}
        llm=FakeLLM()
        def review(system,payload):
            # Markdown contains line breaks. A model may flatten only whitespace.
            phrase=payload['wiki'].strip()
            quote=' '.join(phrase.split())
            if len(quote)>1500:
                return {'findings':[]}
            return {'findings':[dict(severity='warning',quote=quote,issue='조건 검토',suggestion='원문 확인')]}
        llm.complete_json=review
        report=run_lint(self.db,llm=llm)
        self.assertGreater(report['llm_calls'],0)
        with connect(self.db) as db:
            findings=json.loads(db.execute('SELECT findings_json FROM wiki_lint_runs ORDER BY created_at DESC LIMIT 1').fetchone()[0])
        anchored=[f for f in findings if 'start_offset' in f]
        self.assertTrue(anchored)
        for item in anchored:
            _,body=self.store.read(self.store.record(item['page_id']))
            self.assertEqual(body[item['start_offset']:item['end_offset']],item['quote'])
        llm.identity='bad-lint-model'
        llm.complete_json=lambda *a:{'findings':[dict(severity='error',quote='This invented quotation never existed.',issue='Check',suggestion='Review')]}
        run_lint(self.db,llm=llm)
        with connect(self.db) as db:
            findings=json.loads(db.execute('SELECT findings_json FROM wiki_lint_runs ORDER BY created_at DESC LIMIT 1').fetchone()[0])
        self.assertTrue(any(f.get('code')=='quote_not_found' for f in findings))
        for path,content in files.items():self.assertEqual((self.store.root/path).read_bytes(),content)

    def test_lint_reports_manual_damage_without_changing_file(self):
        self.sync();row=self.source();path=self.store.root/row['path']
        path.write_text(path.read_text(encoding='utf-8')+'\nBroken edit\n',encoding='utf-8')
        edited=path.read_bytes();result=run_lint(self.db,use_llm=False)
        self.assertGreater(result['findings'],0);self.assertEqual(path.read_bytes(),edited)

    def test_outbox_rolls_back_with_canonical_transaction_and_lease_excludes_worker(self):
        run_jobs(self.db,self.no_llm)
        with connect(self.db) as db:
            sync_wiki_memberships(db);db.rollback()
            self.assertEqual(db.execute("SELECT count(*) FROM wiki_jobs WHERE state='queued'").fetchone()[0],0)
            enqueue(db,'concurrent work')
            db.execute('INSERT OR REPLACE INTO wiki_worker_lease VALUES(1,?,?)',('other',time.time()+60));db.commit()
        self.assertEqual(run_jobs(self.db,self.no_llm)['status'],'busy')

    def test_repeated_reads_do_not_rebuild_corpus_snapshot(self):
        self.sync()
        with patch('backend.wiki_engine._full_snapshot',side_effect=AssertionError('whole-corpus reload')):
            list_wikis(limit=1);list_wikis(expert='dram',limit=1)

    def test_live_policy_is_shared_and_invalidates_compile_and_lint_caches(self):
        policy=self.root/'policy.md'
        policy.write_text(POLICY_PATH.read_text(encoding='utf-8')+'\nPOLICY_VERSION_ONE\n',encoding='utf-8')
        llm=FakeLLM()
        with patch('backend.wiki_policy.POLICY_PATH',policy):
            self.sync(llm)
            self.assertTrue(llm.systems)
            self.assertTrue(all('POLICY_VERSION_ONE' in s for s in llm.systems))
            source=self.source();source_hash=source['content_hash']
            topic=next(r for r in self.store.rows() if r['kind']=='topic')
            self.assertEqual(json.loads(topic['refs_json'])['policy_hash'],load_prompt('compile').policy_hash)
            llm.calls.clear();self.sync(llm);self.assertEqual(llm.calls,[])
            answer(Question(question='refresh 74',expert_ids=['dram']),llm)
            self.assertIn('POLICY_VERSION_ONE',llm.systems[-1])
            self.assertGreater(run_lint(self.db,llm=llm)['llm_calls'],0)
            self.assertEqual(run_lint(self.db,llm=llm,due_only=True)['status'],'not_due')

            policy.write_text(policy.read_text(encoding='utf-8').replace('POLICY_VERSION_ONE','POLICY_VERSION_TWO'),encoding='utf-8')
            # Even without any document changing, new lint rules invalidate reviews.
            second=run_lint(self.db,llm=llm,due_only=True)
            self.assertGreater(second['llm_calls'],0)
            self.assertIn('POLICY_VERSION_TWO',llm.systems[-1])
            self.sync(llm)
            self.assertEqual(self.store.record(source['id'])['content_hash'],source_hash)
            self.assertEqual(self.store.record(topic['id'])['revision'],topic['revision']+1)
            self.assertIn('POLICY_VERSION_TWO',llm.systems[-1])
            answer(Question(question='refresh 74',expert_ids=['dram']),llm)
            self.assertIn('POLICY_VERSION_TWO',llm.systems[-1])
            self.assertNotIn('POLICY_VERSION_ONE',llm.systems[-1])

    def test_missing_or_empty_policy_prevents_unguided_model_calls(self):
        self.sync();llm=FakeLLM();path=self.root/'missing-policy.md'
        with patch('backend.wiki_policy.POLICY_PATH',path):
            for content in (None,'   '):
                if content is not None:path.write_text(content,encoding='utf-8')
                with self.assertRaises(WikiError):self.sync(llm)
                with self.assertRaises(WikiError):run_lint(self.db,llm=llm)
                result=answer(Question(question='refresh 74',expert_ids=['dram']),llm)
                self.assertEqual(result['answers'][0]['mode'],'evidence')
                self.assertIn('지침',result['answers'][0]['notice'])
                self.assertEqual(llm.calls,[])

    def test_index_is_stable_and_lint_detects_drift_then_sync_repairs_it(self):
        self.sync();path=self.store.root/'index.md'
        body=path.read_text(encoding='utf-8');stamp=path.stat().st_mtime_ns
        self.assertIn('공통 주제',body);self.assertIn('종합 작성 대기',body)
        self.assertIn('논문별 원천 지식',body)
        self.sync();self.assertEqual(path.stat().st_mtime_ns,stamp)
        path.write_text(body+'\n[잘못된 문서](missing.md)\n',encoding='utf-8')
        self.assertGreater(run_lint(self.db,use_llm=False)['findings'],0)
        self.sync();self.assertEqual(path.read_text(encoding='utf-8'),body)
        self.assertEqual(run_lint(self.db,use_llm=False)['findings'],0)
        with connect(self.db) as db:
            db.execute("INSERT INTO expert_deletions VALUES('dram',?)",(now(),));db.commit()
        self.sync()
        self.assertNotIn('experts/dram/index.md',path.read_text(encoding='utf-8'))

    def test_assistant_synthesis_is_kept_until_its_inputs_change(self):
        self.sync();row=next(r for r in self.store.rows() if r['kind']=='topic')
        refs=json.loads(row['refs_json']);body='# DRAM\n\n전문가 검토를 위한 종합 예시.\n'
        published=self.store.publish(row['id'],row['path'],row['title'],body,refs,row['input_hash'],
            kind='topic',status='ready',producer='assistant-synthesis',adopt=True)
        self.sync()
        self.assertEqual(self.store.record(row['id'])['content_hash'],published['content_hash'])
        self.assertIn('종합 작성', (self.store.root/'index.md').read_text(encoding='utf-8'))

    def test_vector_search_reuses_index_and_can_find_without_keyword_match(self):
        from backend.chunking import ChunkPolicy
        from backend.embedding_client import EmbeddingConfig
        from backend.wiki_retrieval import reindex
        self.sync()
        class VectorClient:
            def __init__(self,config):self.requests=0
            def embed(self,texts):
                self.requests+=len(texts)
                return [[1.,0.] if 'refresh' in t.lower() or t=='paraphrase' else [0.,1.] for t in texts]
        service=ModelSettings('http://company.invalid/embedding','test-vector',SecretStr('fake'))
        policy=ChunkPolicy(mode='semantic',embedding=EmbeddingConfig(service))
        with patch('backend.wiki_retrieval.load_chunk_policy',return_value=policy),patch('backend.wiki_retrieval.EmbeddingClient',VectorClient),patch('backend.embedding_client.EmbeddingClient',VectorClient):
            self.assertGreater(reindex(self.db)['embedding_requests'],0)
            self.assertEqual(reindex(self.db)['embedding_requests'],0)
            result,notices=retrieve('paraphrase',['dram'],self.db)
            self.assertTrue(result);self.assertEqual(notices,[])
            self.assertFalse(retrieve('paraphrase',['nor'],self.db)[0])


class CompanyLLMContractTests(unittest.TestCase):
    def test_reranker_uses_shared_key_and_response_indexes(self):
        captured=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append((self.headers.get('Authorization'),json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200);self.end_headers()
                self.wfile.write(json.dumps({'results':[{'index':1,'relevance_score':.9},{'index':0,'relevance_score':.1}]}).encode())
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            service=ModelSettings(f'http://127.0.0.1:{server.server_port}/rerank','bge-reranker',SecretStr('shared-embedding-key'))
            result=rerank('retention',[{'text':'first'},{'text':'second'}],service)
            self.assertEqual(result[0]['text'],'second')
            self.assertEqual(captured[0][0],'Bearer shared-embedding-key')
            self.assertEqual(captured[0][1]['documents'],['first','second'])
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_bearer_messages_and_choices_contract(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path,self.headers.get('Authorization'),json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                body=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'sections':[]})}}]}).encode()
                self.send_response(200);self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=CompanyLLM(ModelSettings(f'http://127.0.0.1:{server.server_port}/v1','intranet-model',SecretStr('test-key')))
            self.assertEqual(client.complete_json('system',{'question':'test'}),{'sections':[]})
            self.assertEqual(received[0][0],'/v1/chat/completions')
            self.assertEqual(received[0][1],'Bearer test-key')
            self.assertEqual(received[0][2]['messages'][0]['role'],'system')
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
