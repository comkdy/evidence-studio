"""HTTP user journeys across expert setup, library, Wiki and research answers."""
import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

import test_markdown_wiki as fixtures
from backend.ai_settings import AISettings, ModelSettings
from backend.main import app
from backend.pilot_db import connect
from backend.pilot_ingest import ingest_file
from backend.wiki_engine import run_jobs
from test_pilot import make_pdf, TEXT


class FeatureConnectionTests(unittest.TestCase):
    curation = fixtures.MarkdownWikiTests.curation
    sync = fixtures.MarkdownWikiTests.sync
    enable_reviewed_model = fixtures.MarkdownWikiTests.enable_reviewed_model
    drain_demand_work = fixtures.MarkdownWikiTests.drain_demand_work

    def setUp(self):
        # Block service use even during the underlying ingestion fixture setup.
        values = {f'{prefix}_{field}': '' for prefix in ('LLM', 'EMBEDDING', 'RERANKER')
                  for field in ('BASE_URL', 'MODEL', 'API_KEY')}
        values['AUTH_MODE'] = 'local_admin'
        self.offline = patch.dict(os.environ, values)
        self.offline.start()
        fixtures.MarkdownWikiTests.setUp(self)
        self.sync()
        self.client = TestClient(app)  # No lifespan worker; explicit fixture sync only.

    def tearDown(self):
        self.client.close()
        fixtures.MarkdownWikiTests.tearDown(self)
        self.offline.stop()

    def request(self, method, path, payload=None, status=200):
        response = self.client.request(method, '/api/pilot'+path, json=payload)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def create(self):
        library = self.request('GET', '/papers?expert=dram&limit=50')
        self.pid = library['papers'][0]['id']
        draft = dict(name='Scoped Research', overview='Retention and refresh evidence',
                     facets={'platform': []}, included_ids=[self.pid], excluded_ids=[], enabled=True)
        preview = self.request('POST', '/experts/preview', draft)
        created = self.request('POST', '/experts', dict(draft, request_id=str(uuid4())), status=201)
        return created['id'], preview

    def test_create_to_library_wiki_and_both_answer_modes_share_the_same_evidence(self):
        with connect(self.db) as db:
            before = {table: db.execute('SELECT count(*) FROM '+table).fetchone()[0]
                      for table in ('papers', 'claims', 'measurements', 'wiki_files', 'wiki_revisions')}
        eid, preview = self.create()
        library = self.request('GET', '/papers?expert='+eid+'&limit=50')
        wiki = self.request('GET', '/wikis?expert='+eid+'&limit=20')
        expert = next(e for e in self.request('GET', '/summary')['experts'] if e['id'] == eid)
        self.assertEqual([p['id'] for p in library['papers']], [self.pid])
        self.assertEqual(expert['paper_count'], library['total'])
        self.assertEqual(expert['wiki_count'], wiki['total'])
        self.assertEqual(preview['counts']['wikis'], wiki['total'])
        self.assertTrue(wiki['wikis'])
        self.assertTrue(all(w['kind'] in ('source', 'topic', 'specialization') for w in wiki['wikis']))
        for item in wiki['wikis']:
            details = self.request('GET', '/wikis/'+item['id'])
            self.assertTrue(all(c['paper_id'] == self.pid for c in details['claims']))
            raw = self.client.get('/api/pilot/wiki-files/'+item['id']+'/markdown')
            self.assertEqual(raw.status_code, 200)
        paper = self.request('GET', '/papers/'+self.pid)
        self.assertEqual(paper['claims'][0]['measurements'][0]['raw_value'], '74%')
        self.assertIn(eid,{expert['id'] for expert in paper['knowledge']['experts']})
        self.assertTrue(paper['knowledge']['common_wikis'])
        self.assertTrue(all(w['scope']=='common' and w['kind'] in ('source','topic')
                            for w in paper['knowledge']['common_wikis']))
        pdf = self.client.get('/api/pilot/documents/'+paper['document_id']+'/pdf')
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        for mode_fields in ({}, {'mode':'deep'}):
            result = self.request('POST', '/ask', dict(question='refresh 74', expert_ids=[eid], **mode_fields))
            self.assertEqual(result['research']['mode'], 'deep')
            self.assertEqual({c['paper_id'] for c in result['claims']}, {self.pid})
            self.assertEqual(result['synthesis']['mode'], 'evidence')
            saved = self.request('GET', '/answer-runs/'+result['run_id'])
            self.assertEqual(saved['result'], result)
            self.assertEqual(saved['trace']['model_calls'], [])
        with connect(self.db) as db:
            after = {table: db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in before}
        self.assertEqual(before, after, 'Expert setup must reuse canonical evidence and Wiki files')

    def test_scope_edit_immediately_changes_library_wiki_and_answer_before_worker_runs(self):
        eid, _ = self.create()
        config = self.request('GET', '/experts/'+eid)
        draft = dict(config['draft'], included_ids=[], excluded_ids=[self.pid])
        self.request('PUT', '/experts/'+eid, dict(draft, revision=config['revision']))
        self.assertEqual(self.request('GET', '/papers?expert='+eid+'&limit=50')['total'], 0)
        self.assertEqual(self.request('GET', '/wikis?expert='+eid+'&limit=20')['total'], 0)
        expert = next(e for e in self.request('GET', '/summary')['experts'] if e['id'] == eid)
        self.assertEqual((expert['paper_count'], expert['claim_count'], expert['wiki_count']), (0, 0, 0))
        result = self.request('POST', '/ask', dict(question='refresh 74', expert_ids=[eid]))
        self.assertEqual(result['claims'], [])
        self.assertEqual(result['hits'], [])
        self.assertEqual(result['research']['paper_count'], 0)
        self.assertTrue(self.request('GET', '/papers/'+self.pid)['claims'])

    def test_new_paper_flows_through_saved_membership_queue_and_raw_answer_index(self):
        created = self.request('POST', '/experts', dict(name='Growing DRAM', overview='DRAM research',
            facets={'platform': ['DRAM']}, included_ids=[], excluded_ids=[], enabled=True,
            request_id=str(uuid4())), status=201)
        eid = created['id']
        incoming = self.root/'new-retention.pdf'
        make_pdf(incoming, TEXT.replace('Retention Study', 'Long Retention Study')+
                 '\nThe longretentionmarker experiment tests a separate DRAM array.')
        self.enable_reviewed_model()
        ingested=ingest_file(incoming, self.db, 0)
        self.assertEqual(ingested['status'], 'ready')
        with patch('backend.llm_client.CompanyLLM.complete_json',side_effect=AssertionError('Read must not call model')):
            pending=self.request('GET','/papers/'+ingested['paper_id'])
        self.assertEqual(pending['claims'],[])
        self.assertEqual(pending['knowledge']['coverage']['status'],'pending')
        self.assertEqual(pending['knowledge']['common_wikis'],[])
        library = self.request('GET', '/papers?expert='+eid+'&limit=50')
        self.assertEqual(library['total'], 2)
        with connect(self.db) as db:
            self.assertTrue(db.execute("SELECT 1 FROM work_tasks WHERE kind='knowledge.extract' AND state='queued'").fetchone())
            self.assertFalse(db.execute("SELECT 1 FROM wiki_jobs WHERE state='queued'").fetchone())
        self.drain_demand_work()
        wiki = self.request('GET', '/wikis?expert='+eid+'&limit=20')
        with connect(self.db) as db:
            sources=[r['refs_json'] for r in db.execute("SELECT refs_json FROM wiki_files WHERE kind='source' AND status='ready'")]
        self.assertEqual(len({pid for source in sources for pid in json.loads(source)['paper_ids']}),2)
        self.assertTrue(any(w['kind']=='source' for w in wiki['wikis']))
        with patch('backend.llm_client.CompanyLLM.complete_json',side_effect=AssertionError('Read must not call model')):
            reviewed=self.request('GET','/papers/'+ingested['paper_id'])
        self.assertEqual(reviewed['claims'],[])
        self.assertGreater(reviewed['knowledge']['coverage']['ready_chunks'],0)
        self.assertEqual(reviewed['knowledge']['coverage']['review_state'],'model_reviewed_human_pending')
        self.assertTrue(any(w['kind']=='source' for w in reviewed['knowledge']['common_wikis']))
        self.assertIn(eid,{expert['id'] for expert in reviewed['knowledge']['experts'] if expert['enabled']})
        result = self.request('POST', '/ask', dict(question='longretentionmarker', expert_ids=[eid]))
        self.assertTrue(result['hits'])
        self.assertTrue(any('longretentionmarker' in hit['text'] for hit in result['hits']))
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM claims').fetchone()[0], 1)
            self.assertFalse(db.execute("SELECT 1 FROM wiki_jobs WHERE state IN ('queued','running','failed')").fetchone())

    def test_summary_configuration_flags_match_settings_without_model_requests_or_secrets(self):
        service = ModelSettings('https://internal.invalid', 'test-only', SecretStr('fixture-secret'))
        settings = AISettings(llm=service, embedding=service, reranker=service)
        with patch('backend.ai_settings.load_ai_settings', return_value=settings), \
             patch('backend.llm_client.CompanyLLM.complete_json', side_effect=AssertionError('Unexpected model call')):
            response = self.request('GET', '/summary')
        self.assertTrue(response['llm_enabled'])
        self.assertTrue(response['embedding_enabled'])
        self.assertNotIn('fixture-secret', str(response))
        self.assertNotIn('internal.invalid', str(response))


if __name__ == '__main__':
    unittest.main()
