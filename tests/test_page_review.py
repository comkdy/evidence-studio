import json
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from uuid import uuid4
from fastapi.testclient import TestClient
from backend.main import app
from backend.pilot_db import connect
from backend.pilot_ingest import ingest_file
from backend.pilot_curate import apply_curation
from backend.chunking import reindex_document,load_chunk_policy
from backend.sso_adapter import EmployeeProfile
from backend.wiki_engine import run_jobs
from backend.wiki_retrieval import retrieve
from backend.pilot_api import search
import test_processing as processing_fixtures
import test_pilot as pilot_fixtures
from test_pilot import TEXT
from test_markdown_wiki import FakeLLM


class PageReviewTests(unittest.TestCase):
    pdf=processing_fixtures.ProcessingTests.pdf
    curation=pilot_fixtures.PilotTests.curation
    def setUp(self):
        processing_fixtures.ProcessingTests.setUp(self)
        self.pdf=self.pdf('real.pdf')
        self.assertEqual(ingest_file(self.pdf,self.db,0)['status'],'ready')
        with connect(self.db) as db:
            page=db.execute('SELECT * FROM pages').fetchone();self.page_id=page['id']
            db.execute("UPDATE pages SET status='needs_review'")
            reindex_document(db,page['run_id'],load_chunk_policy());db.commit()
        self.client=TestClient(app,headers={'Origin':'http://testserver'})
        self.url='/api/pilot/page-reviews/'+self.page_id

    def tearDown(self):
        self.client.close();processing_fixtures.ProcessingTests.tearDown(self)

    def detail(self):
        response=self.client.get(self.url);self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def draft(self,action='correct',**values):
        return {'request_id':str(uuid4()),'revision':self.detail()['revision'],'action':action,
                'text':TEXT+' Corrected caption: quasiparticle retention measured at 90 C.' if action=='correct' else '',
                'reason':'원문과 대조하여 텍스트 보완','confirmed':True}|values

    def sso(self):
        self.client.cookies.set('company_sso','test-cookie')
        stack=ExitStack()
        stack.enter_context(patch.dict(os.environ,{'AUTH_MODE':'sso','SSO_COOKIE_NAME':'company_sso'}))
        stack.enter_context(patch('backend.sso_adapter.verify_sso_cookie',return_value='3000001'))
        stack.enter_context(patch('backend.sso_adapter.fetch_employee_profile',return_value=EmployeeProfile('3000001','검토자','연구팀')))
        return stack

    def test_diagnosis_reports_real_character_problem(self):
        with connect(self.db) as db:
            db.execute("UPDATE pages SET text='bad\x01text\ufffd',status='needs_review',char_count=9");db.commit()
        problem=self.detail()['problem']
        self.assertEqual(problem['suspicious_characters'],2)
        self.assertTrue(problem['snippets'])
        self.assertIn('2개',problem['reason'])
        with connect(self.db) as db:
            db.execute("UPDATE pages SET text=' \n',status='needs_ocr',char_count=2");db.commit()
        self.assertEqual(self.detail()['problem']['characters'],0)
        self.assertEqual(self.client.get('/api/pilot/status').json()['issues'][0]['char_count'],0)

    def test_correction_changes_search_preserves_original_and_queues_wiki(self):
        original=self.detail()['page']['text'];draft=self.draft()
        response=self.client.post(self.url,json=draft)
        self.assertEqual(response.status_code,201,response.text);self.assertEqual(response.json()['state'],'applied')
        detail=self.detail();self.assertEqual(detail['page']['status'],'ready')
        self.assertEqual(detail['original_text'],original);self.assertEqual(detail['history'][0]['reason'],draft['reason'])
        self.assertTrue(search('quasiparticle',expert='',limit=10)['hits'])
        self.assertEqual(self.client.get('/api/pilot/status').json()['issues'],[])
        self.assertEqual(self.client.get('/api/pilot/processing').json()['review_pages'],0)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM page_review_changes').fetchone()[0],1)
            self.assertIsNone(response.json()['wiki_job_id'])
            self.assertTrue(db.execute("SELECT 1 FROM work_tasks WHERE kind='knowledge.publish_source' AND priority=20 AND state='queued'").fetchone())
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertTrue(retrieve('quasiparticle',['dram'],self.db,local_only=True)[0])

    def test_duplicate_save_is_idempotent_and_conflict_preserves_draft(self):
        draft=self.draft();old_revision=draft['revision']
        self.assertEqual(self.client.post(self.url,json=draft).status_code,201)
        self.assertEqual(self.client.post(self.url,json=draft).status_code,201)
        self.assertEqual(self.client.post(self.url,json=draft|{'text':TEXT}).status_code,409)
        stale=self.draft(revision=old_revision,reason='오래된 화면에서 저장')
        self.assertEqual(self.client.post(self.url,json=stale).status_code,409)
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT count(*) FROM page_review_changes').fetchone()[0],1)

    def test_confirm_exact_text_and_exclude_reopen_have_different_effects(self):
        before=self.detail()['page']['text']
        self.assertEqual(self.client.post(self.url,json=self.draft('verify')).status_code,201)
        self.assertEqual(self.detail()['page']['text'],before)
        self.assertEqual(self.client.post(self.url,json=self.draft('exclude',reason='검색에서 제외할 페이지 확인')).status_code,201)
        self.assertEqual(self.detail()['page']['status'],'excluded')
        self.assertFalse(search('retention',expert='',limit=10)['hits'])
        status=self.client.get('/api/pilot/status').json()
        self.assertFalse(status['issues']);self.assertEqual(status['reviewed_pages'][0]['status'],'excluded')
        self.assertEqual(self.client.post(self.url,json=self.draft('reopen',reason='내용 보완을 위해 다시 검토')).status_code,201)
        self.assertEqual(self.client.get('/api/pilot/processing').json()['review_pages'],1)
        self.assertEqual(self.detail()['original_text'],before)

    def test_page_number_or_corrupted_text_cannot_be_marked_complete(self):
        for text in ('4','Broken \ufffd text from the original document.','Broken \x01 text from original document.'):
            self.assertEqual(self.client.post(self.url,json=self.draft(text=text)).status_code,422)
        self.assertEqual(self.client.post(self.url,json=self.draft(confirmed=False)).status_code,422)
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT count(*) FROM page_review_changes').fetchone()[0],0)

    def test_search_failure_rolls_back_text_history_and_request(self):
        before=self.detail()
        with patch('backend.page_review.reindex_document',side_effect=RuntimeError('private address must not leak')):
            response=self.client.post(self.url,json=self.draft())
        self.assertEqual(response.status_code,503);self.assertNotIn('private address',response.text)
        after=self.detail();self.assertEqual(before,after)

    def test_regular_user_proposes_then_manager_applies(self):
        before=self.detail()['page']
        with self.sso():
            self.assertFalse(self.detail()['can_apply'])
            draft=self.draft();response=self.client.post(self.url,json=draft)
            self.assertEqual(response.status_code,201,response.text);self.assertEqual(response.json()['state'],'pending')
            self.assertEqual(self.detail()['page'],before)
            url='/api/pilot/page-review-requests/'+response.json()['id']+'/decision'
            decision={'action':'apply','revision':draft['revision'],'confirmed':True}
            self.assertEqual(self.client.post(url,json=decision).status_code,403)
        self.assertEqual(self.client.get('/api/pilot/status').json()['issues'][0]['pending_requests'],1)
        applied=self.client.post(url,json=decision);self.assertEqual(applied.status_code,200,applied.text)
        self.assertEqual(self.detail()['page']['status'],'ready')
        self.assertEqual(self.detail()['history'][0]['actor_name'],'검토자')

    def test_stale_proposal_cannot_override_new_correction_and_can_be_rejected(self):
        with self.sso():
            draft=self.draft();response=self.client.post(self.url,json=draft)
        url='/api/pilot/page-review-requests/'+response.json()['id']+'/decision'
        self.client.post(self.url,json=self.draft(text=TEXT+' A newer correction.'))
        self.assertEqual(self.client.post(url,json={'action':'apply','revision':self.detail()['revision'],'confirmed':True}).status_code,409)
        self.assertEqual(self.client.post(url,json={'action':'reject','revision':self.detail()['revision'],'note':'이미 최신 텍스트로 보완했습니다.'}).status_code,200)
        self.assertEqual(self.detail()['page']['text'],TEXT+' A newer correction.')

    def test_superuser_can_apply_knowledge_correction(self):
        with self.sso():self.detail()
        with connect(self.db) as db:db.execute("UPDATE users SET role='superuser' WHERE employee_id='3000001'");db.commit()
        with self.sso():
            self.assertTrue(self.detail()['can_apply'])
            self.assertEqual(self.client.post(self.url,json=self.draft()).json()['state'],'applied')

    def test_existing_citations_remain_exact_and_changed_values_are_rejected(self):
        target,_=self.curation();apply_curation(target,self.db)
        with connect(self.db) as db:before=dict(db.execute('SELECT * FROM evidence_spans LIMIT 1').fetchone())
        changed=TEXT.replace('74 percent','75 percent')
        self.assertEqual(self.client.post(self.url,json=self.draft(text=changed)).status_code,409)
        self.assertEqual(self.client.post(self.url,json=self.draft('exclude')).status_code,409)
        response=self.client.post(self.url,json=self.draft(text='Restored heading.\n'+TEXT))
        self.assertEqual(response.status_code,201,response.text)
        with connect(self.db) as db:
            span=db.execute('SELECT * FROM evidence_spans WHERE id=?',(before['id'],)).fetchone()
            text=db.execute('SELECT text FROM pages WHERE id=?',(self.page_id,)).fetchone()[0]
            self.assertEqual(span['quote'],before['quote']);self.assertEqual(text[span['start_offset']:span['end_offset']],span['quote'])
            offsets=json.loads(db.execute('SELECT span_offsets_json FROM page_review_changes').fetchone()[0])
            self.assertEqual(offsets[0]['before'],[before['start_offset'],before['end_offset']])
        run_jobs(self.db,FakeLLM())

    def test_original_pdf_and_canonical_claims_are_not_rewritten(self):
        before=self.pdf.read_bytes()
        self.client.post(self.url,json=self.draft())
        self.assertEqual(self.pdf.read_bytes(),before)
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT count(*) FROM claims').fetchone()[0],0)
