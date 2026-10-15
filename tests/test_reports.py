"""Report editing contracts: narrow context, immutable evidence, privacy and cancellation."""
import asyncio
import copy
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
import test_auth
from backend.answer_trace import save_run
from backend.pilot_db import connect
from backend.report_api import EditRequest, stream_edit
from backend.report_engine import edit_section
from backend.report_export import render_html
from backend.report_store import create_report, read_report, save_report, focus_context
from backend.research_progress import ResearchControl, ResearchCancelled, _current, checkpoint
from backend.wiki_budget import WikiBudget


class ReportLLM:
    configured = True
    service = SimpleNamespace(model='isolated-report-model')
    def __init__(self, transform=None):
        self.calls = []
        self.usage_history = [dict(total_tokens=123)]
        self._wiki_budget = WikiBudget(64000, 2048)
        self.transform = transform
    def complete_json(self, system, payload, **kwargs):
        self.calls.append(copy.deepcopy(payload))
        key = next(iter(payload['evidence']))
        result = dict(sections=[dict(heading='근거와 해석', statements=[dict(
            kind='finding', text='At 85 °C, retention time was 64 ms.',
            evidence_ids=[key], quotes={key:'At 85 °C, retention time was 64 ms.'})])])
        return self.transform(result) if self.transform else result


class ReportTests(unittest.TestCase):
    setUp = test_auth.IdentityTests.setUp
    tearDown = test_auth.IdentityTests.tearDown
    sso = test_auth.IdentityTests.sso

    def seed(self):
        evidence = {'page': dict(kind='source_text', paper_id='paper', page_id='page1',
            document_id='document', page_number=2, offset=100,
            text='Test result. At 85 °C, retention time was 64 ms. End of result.')}
        save_run('run', SimpleNamespace(question='retention', mode='deep'), model_identity='test',
                 prompt=None, evidence=evidence, trace={}, result=dict(claims=[], hits=[dict(paper_id='paper', title='Retention study')]), elapsed_ms=0)
        report = create_report('Retention report', 'Team review')
        return report, EditRequest(revision=1, section_id=report['document']['sections'][0]['id'],
                                   instruction='Summarize the retention measurement', run_ids=['run'])

    def test_selected_section_only_and_no_search_without_opt_in(self):
        report, request = self.seed()
        doc = copy.deepcopy(report['document'])
        doc['sections'][1]['statements'] = [dict(text='OTHER SECTION PRIVATE SENTINEL')]
        report = save_report(report['id'], 1, doc, 'fixture')
        request.revision = 2
        llm = ReportLLM()
        with patch('backend.wiki_api.answer') as research:
            edited = edit_section(report['id'], request, llm)
        research.assert_not_called()
        self.assertEqual(len(llm.calls), 1)
        self.assertNotIn('OTHER SECTION PRIVATE SENTINEL', json.dumps(llm.calls))
        self.assertEqual(edited['document']['sections'][1:], doc['sections'][1:])
        self.assertEqual(edited['revision'], 3)
        target = edited['document']['sections'][0]
        self.assertEqual(target['statements'][0]['source_spans'][0]['start_offset'], 113)
        self.assertEqual(next(iter(target['sources'].values()))['answer_run_id'], 'run')
        self.assertEqual(read_report(report['id'], 1)['document']['sections'][0]['statements'], [])
        # A subsequent edit uses the section's stored evidence without chat history or search.
        request.revision, request.run_ids = 3, []
        self.assertEqual(edit_section(report['id'], request, ReportLLM())['revision'], 4)

    def test_regular_user_can_author_but_other_owner_including_admin_cannot_read(self):
        with self.sso('3000001'):
            response = self.client.post('/api/pilot/reports', json=dict(title='Private report'),headers={'Origin':'http://testserver'})
            self.assertEqual(response.status_code, 201, response.text)
            report = response.json()
            self.assertEqual(len(self.client.get('/api/pilot/reports').json()), 1)
        for suffix in ['', '/versions', '/export']:
            self.assertEqual(self.client.get('/api/pilot/reports/'+report['id']+suffix).status_code, 404)
        self.assertEqual(self.client.post('/api/pilot/reports/'+report['id']+'/restore', json=dict(revision=1,version=1)).status_code,404)
        with patch('backend.report_api.edit_section') as llm:
            response=self.client.post('/api/pilot/reports/'+report['id']+'/edit-stream',json=dict(revision=1,section_id='x',instruction='edit'))
        self.assertEqual(response.status_code,404);llm.assert_not_called()

    def test_foreign_answer_evidence_rejected_before_model(self):
        report, request = self.seed()
        with connect(self.db) as db:
            db.execute("UPDATE answer_runs SET owner_id='9999999'");db.commit()
        llm = ReportLLM()
        with self.assertRaises(HTTPException) as error:
            edit_section(report['id'], request, llm)
        self.assertEqual(error.exception.status_code,404);self.assertFalse(llm.calls)

    def test_shape_layout_delete_and_restore_are_versioned_without_llm(self):
        report, _ = self.seed();url='/api/pilot/reports/'+report['id']
        added=self.client.patch(url+'/sections',json=dict(revision=1,action='add',title='Comparison',layout='table')).json()
        self.assertEqual(len(added['document']['sections']),5)
        self.assertEqual(added['document']['sections'][-1]['layout'],'table')
        deleted=self.client.patch(url+'/sections',json=dict(revision=2,action='delete',section_id=added['document']['sections'][-1]['id'])).json()
        self.assertEqual(len(deleted['document']['sections']),4)
        restored=self.client.post(url+'/restore',json=dict(revision=3,version=2)).json()
        self.assertEqual(restored['revision'],4);self.assertEqual(restored['document'],added['document'])
        self.assertEqual(self.client.post(url+'/restore',json=dict(revision=3,version=1)).status_code,409)
        self.assertEqual(len(self.client.get(url+'/versions?before=3').json()),2)
        self.assertEqual(self.client.get(url+'?version=2').json()['document'],added['document'])

    def test_invalid_quote_quantity_or_missing_evidence_keeps_existing_document(self):
        report, request=self.seed()
        def corrupt(field,value):
            def change(result):result['sections'][0]['statements'][0][field]=value;return result
            return change
        transforms=[corrupt('text','Retention time was 64 ns.'),corrupt('text','Retention time was 128 ms.'),
                    corrupt('quotes',{}),corrupt('evidence_ids',['invented']),lambda r:dict(sections=[])]
        for transform in transforms:
            with self.subTest(transform=transform),self.assertRaises(HTTPException):
                edit_section(report['id'],request,ReportLLM(transform))
            self.assertEqual(read_report(report['id'])['revision'],1)

    def test_inflight_llm_does_not_hold_write_lock_and_stale_edit_cannot_overwrite(self):
        report,request=self.seed()
        def concurrent(result):
            # A separate connection can write while the fake LLM is running.
            with connect(self.db) as db:
                db.execute("UPDATE users SET team='parallel write'");db.commit()
            doc=copy.deepcopy(report['document']);doc['title']='Concurrent edit'
            save_report(report['id'],1,doc,'Another tab')
            return result
        with self.assertRaises(HTTPException) as error:edit_section(report['id'],request,ReportLLM(concurrent))
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(read_report(report['id'])['document']['title'],'Concurrent edit')

    def test_cancelled_generation_never_publishes(self):
        report,request=self.seed();control=ResearchControl(lambda e:None)
        def cancel(result):control.cancelled.set();return result
        token=_current.set(control)
        try:
            with self.assertRaises(ResearchCancelled):edit_section(report['id'],request,ReportLLM(cancel))
        finally:_current.reset(token)
        self.assertEqual(read_report(report['id'])['revision'],1)

    def test_focus_context_is_owner_checked_selected_and_not_source_evidence(self):
        report,request=self.seed()
        doc=copy.deepcopy(report['document']);doc['sections'][0]['statements']=[dict(text='selected context')];doc['sections'][1]['statements']=[dict(text='excluded context')]
        save_report(report['id'],1,doc,'fixture')
        context=focus_context(report['id'],request.section_id,2)
        self.assertIn('selected context',str(context));self.assertNotIn('excluded context',str(context));self.assertIn('근거가 아님',str(context))
        with self.assertRaises(HTTPException):focus_context(report['id'],request.section_id,1)
        with self.assertRaises(HTTPException):focus_context(None,request.section_id,None)
        with patch('backend.report_store.current_user',return_value={'employee_id':'other'}),self.assertRaises(HTTPException):focus_context(report['id'],request.section_id,2)

    def test_extra_research_is_explicit_and_returns_new_owned_run(self):
        report,request=self.seed();request.research=True;request.run_ids=[]
        with patch('backend.wiki_api.recommendations',return_value={'experts':[{'id':'dram'}]}),patch('backend.wiki_api.answer',return_value={'run_id':'run'}) as research:
            result=edit_section(report['id'],request,ReportLLM())
        self.assertEqual(research.call_count,1)
        self.assertEqual(research.call_args.args[0].report_section_id,request.section_id)
        self.assertEqual(result['usage']['research_run_id'],'run')

    def test_no_configuration_or_input_budget_does_not_publish(self):
        report,request=self.seed();llm=ReportLLM();llm.configured=False
        with self.assertRaises(HTTPException) as error:edit_section(report['id'],request,llm)
        self.assertEqual(error.exception.status_code,503)
        llm=ReportLLM();llm._wiki_budget=WikiBudget(3000,2048)
        with self.assertRaises(HTTPException):edit_section(report['id'],request,llm)
        self.assertFalse(llm.calls);self.assertEqual(read_report(report['id'])['revision'],1)

    def test_html_exports_exact_version_with_escaped_text_and_snapshot_quotes(self):
        report,request=self.seed();edited=edit_section(report['id'],request,ReportLLM())
        edited['document']['title']='<script>alert(1)</script>'
        html=render_html(edited)
        self.assertNotIn('<script',html);self.assertIn('&lt;script&gt;',html)
        self.assertIn('At 85 °C, retention time was 64 ms.',html)
        self.assertIn('href="#ref-1"',html);self.assertIn('<style>',html)
        response=self.client.get('/api/pilot/reports/'+report['id']+'/export?revision=1')
        self.assertIn('attachment;',response.headers['content-disposition'])
        self.assertNotIn('At 85',response.text)
        self.assertIn('작성 전',response.text)

    def test_stream_reports_actual_progress_and_committed_revision(self):
        report,request=self.seed()
        with patch('backend.report_api.edit_section',side_effect=lambda rid,req:edit_section(rid,req,ReportLLM())):
            response=self.client.post('/api/pilot/reports/'+report['id']+'/edit-stream',json=request.model_dump())
        events=[json.loads(line) for line in response.text.splitlines() if line.strip()]
        self.assertEqual(events[-1]['type'],'report_result')
        self.assertEqual(events[-1]['result']['revision'],2)
        self.assertEqual([e['stage'] for e in events if e['type']=='progress'],['report_context','report_write','report_verify','report_save'])

    def test_disconnect_releases_shared_slot_and_does_not_publish(self):
        report,request=self.seed();entered=threading.Event();release=threading.Event();cancelled=threading.Event();slot=threading.BoundedSemaphore(1)
        def work(rid,req):
            entered.set();release.wait(3)
            try:checkpoint()
            except ResearchCancelled:cancelled.set();raise
            return read_report(rid)
        async def scenario():
            with patch('backend.report_api._slots',slot),patch('backend.report_api.edit_section',work):
                response=await stream_edit(report['id'],request)
                await response.body_iterator.__anext__()
                await response.body_iterator.aclose()
                release.set()
                self.assertTrue(await asyncio.to_thread(cancelled.wait,3))
                self.assertTrue(await asyncio.to_thread(slot.acquire,True,3))
                slot.release()
        asyncio.run(scenario())
        self.assertEqual(read_report(report['id'])['revision'],1)
