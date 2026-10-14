"""Conversation context isolation, observable stages, and cooperative stop contracts."""
import asyncio
import json
import threading
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

import test_markdown_wiki as fixtures
import test_research_answers as research
from backend.answer_trace import read_run
from backend.conversation_context import conversation_context
from backend.main import app
from backend.pilot_db import connect
from backend.research_progress import ResearchCancelled, ResearchControl, _current, checkpoint, stage
from backend.research_stream import ConversationRequest, ask_stream
from backend.wiki_api import Question, answer


class ConversationTests(unittest.TestCase):
    curation = fixtures.MarkdownWikiTests.curation
    sync = fixtures.MarkdownWikiTests.sync

    def setUp(self):
        fixtures.MarkdownWikiTests.setUp(self)
        self.sync()

    def tearDown(self):
        fixtures.MarkdownWikiTests.tearDown(self)

    def ask(self, **kwargs):
        return answer(Question(question='refresh 74', expert_ids=['dram'], **kwargs), research.ResearchLLM())

    def test_followup_uses_owned_prior_answer_and_retrieves_again(self):
        first = self.ask()
        llm = research.ResearchLLM()
        second = answer(Question(question='그 결과의 적용 조건은?', expert_ids=['dram'],
                                 history_run_ids=[first['run_id']]), llm)
        self.assertEqual(llm.operations, ['plan', 'refine', 'answer', 'review'])
        for payload in llm.calls:
            self.assertEqual(payload['conversation_context'][0]['question'], 'refresh 74')
            self.assertEqual(payload['conversation_context'][0]['answer'], first['synthesis']['markdown'])
        run = read_run(second['run_id'])
        self.assertEqual(run['trace']['history_run_ids'], [first['run_id']])
        self.assertTrue(any('이전 질문의 주제' in q for q in second['research']['queries']))
        self.assertTrue(run['evidence'])
        self.assertEqual(read_run(first['run_id'])['trace']['history_run_ids'], [])

    def test_other_owners_history_rejected_before_stream_or_model_call(self):
        first = self.ask()
        with patch('backend.answer_trace.current_user', return_value={'employee_id': '3000000'}), \
             patch('backend.research_stream.answer') as model:
            with TestClient(app) as client:
                response = client.post('/api/pilot/ask-stream', json={
                    'question': 'Follow up', 'history_run_ids': [first['run_id']]})
            self.assertEqual(response.status_code, 404)
            model.assert_not_called()

    def test_bounded_context_rejects_duplicates_and_superseded_answers(self):
        first = self.ask()
        with self.assertRaises(HTTPException) as caught:
            conversation_context([first['run_id']] * 2)
        self.assertEqual(caught.exception.status_code, 422)
        with connect(self.db) as db:
            db.execute("UPDATE answer_runs SET status='superseded' WHERE id=?", (first['run_id'],))
            db.commit()
        with self.assertRaises(HTTPException) as caught:
            conversation_context([first['run_id']])
        self.assertEqual(caught.exception.status_code, 409)

    def test_progress_tracks_executed_stages(self):
        events = []
        token = _current.set(ResearchControl(events.append))
        try:
            self.ask()
        finally:
            _current.reset(token)
        stages = [event['stage'] for event in events]
        for expected in ['context', 'search', 'plan', 'evidence', 'refine', 'answer', 'review', 'verify']:
            self.assertIn(expected, stages)
        self.assertLess(stages.index('answer'), stages.index('review'))
        self.assertEqual(stages[-1], 'verify')
        self.assertTrue(all(set(event) == {'type', 'stage', 'message'} for event in events))

    def test_stop_after_inflight_model_returns_prevents_next_call_and_saved_answer(self):
        control = ResearchControl(lambda event: None)
        def stop(payload, response):
            control.cancelled.set()
            return response
        llm = research.ResearchLLM(plan=stop)
        token = _current.set(control)
        try:
            with self.assertRaises(ResearchCancelled):
                answer(Question(question='refresh 74', expert_ids=['dram']), llm)
        finally:
            _current.reset(token)
        self.assertEqual(llm.operations, ['plan'])
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM answer_runs').fetchone()[0], 0)

    def test_stream_returns_experts_stages_and_final_answer_in_order(self):
        with patch('backend.research_stream.answer', side_effect=lambda req: answer(req, research.ResearchLLM())):
            with TestClient(app) as client:
                response = client.post('/api/pilot/ask-stream', json={
                    'question': 'refresh 74', 'selection': 'manual', 'expert_ids': ['dram']})
        self.assertEqual(response.status_code, 200)
        self.assertIn('application/x-ndjson', response.headers['content-type'])
        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        self.assertEqual(events[-1]['type'], 'result')
        self.assertIn('dram', next(e for e in events if e['type'] == 'experts')['ids'])
        self.assertIn('review', [e.get('stage') for e in events])
        self.assertEqual(read_run(events[-1]['result']['run_id'])['question'], 'refresh 74')

    def test_auto_recommendation_uses_prior_topic(self):
        first = self.ask()
        with patch('backend.research_stream.recommendations', return_value={'experts': []}) as recommend:
            with TestClient(app) as client:
                response = client.post('/api/pilot/ask-stream', json={
                    'question': '이때 조건은?', 'history_run_ids': [first['run_id']]})
        self.assertIn('refresh 74', recommend.call_args.args[0])
        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(events[-1]['status'], 422)

    def test_disconnect_signals_worker_and_releases_concurrency_slot(self):
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        slot = threading.BoundedSemaphore(1)
        def work(request):
            stage('answer', 'Test stage')
            entered.set()
            release.wait(3)
            try:
                checkpoint()
            except ResearchCancelled:
                cancelled.set()
                raise
            return {'claims': [], 'hits': []}
        async def scenario():
            with patch('backend.research_stream._slots', slot), patch('backend.research_stream.answer', work):
                response = await ask_stream(ConversationRequest(question='refresh', selection='manual', expert_ids=['dram']))
                iterator = response.body_iterator
                await anext(iterator)
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                try:
                    with self.assertRaises(HTTPException) as caught:
                        await ask_stream(ConversationRequest(question='refresh'))
                    self.assertEqual(caught.exception.status_code, 429)
                    await iterator.aclose()
                    release.set()
                    self.assertTrue(await asyncio.to_thread(cancelled.wait, 3))
                    self.assertTrue(await asyncio.to_thread(slot.acquire, True, 3))
                    slot.release()
                finally:
                    release.set()
                    await iterator.aclose()
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
