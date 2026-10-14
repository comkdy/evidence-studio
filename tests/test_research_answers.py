"""Research-answer contracts against isolated PDFs/SQLite and deterministic models."""
import copy
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.answer_trace import read_run
from backend.identity_settings import bootstrap_admin_id
from backend.answer_validation import grounded_sections
from backend.llm_client import LLMError
from backend.main import app
from backend.pilot_curate import apply_curation
from backend.pilot_db import connect
from backend.pilot_ingest import ingest_file, sha256_file
from backend.wiki_api import Question, answer
from backend.wiki_store import WikiError
import test_markdown_wiki as markdown_fixtures
from test_pilot import make_pdf


def operation(payload):
    if 'statements' in payload:
        return 'review'
    if 'queries' in payload:
        return 'refine'
    return 'answer' if 'evidence' in payload else 'plan'


class ResearchLLM(markdown_fixtures.FakeLLM):
    def __init__(self, **responses):
        super().__init__()
        self.responses = responses
        self.operations = []

    def complete_json(self, system, payload):
        result = super().complete_json(system, payload)
        name = operation(payload)
        self.operations.append(name)
        override = self.responses.get(name)
        if isinstance(override, Exception):
            raise override
        if callable(override):
            return override(payload, result)
        return copy.deepcopy(override) if name in self.responses else result


class ResearchAnswerTests(unittest.TestCase):
    curation = markdown_fixtures.MarkdownWikiTests.curation
    sync = markdown_fixtures.MarkdownWikiTests.sync

    def setUp(self):
        markdown_fixtures.MarkdownWikiTests.setUp(self)
        self.sync()

    def tearDown(self):
        markdown_fixtures.MarkdownWikiTests.tearDown(self)

    def ask(self, llm=None, mode='deep', experts=None):
        return answer(Question(question='refresh 74', expert_ids=experts or ['dram'], mode=mode), llm or ResearchLLM())

    def add_nor(self):
        path = self.root / 'nor.pdf'
        quote = 'The refresh model estimated 28 percent power reduction in NOR.'
        make_pdf(path, 'NOR Refresh Study\n\nJ. Author\nAbstract - NOR refresh was modeled.\nINTRODUCTION\n'+quote)
        ingest_file(path, self.db, 0)
        payload = {'papers': [{'filename': 'nor.pdf', 'sha256': sha256_file(path), 'experts': ['nor'],
            'claims': [{'id': 'nor-claim', 'summary': quote, 'kind': 'estimate', 'scope': 'NOR model estimate',
                'sources': [{'page': 1, 'quote': quote, 'locator': '본문'}], 'measurements': []}]}], 'wikis': []}
        target = self.root / 'nor-curation.json'
        target.write_text(json.dumps(payload), encoding='utf-8')
        apply_curation(target, self.db)
        self.sync()

    def test_questions_run_deep_workflow_and_produce_private_trace(self):
        llm = ResearchLLM()
        result = self.ask(llm)
        self.assertEqual(llm.operations, ['plan', 'refine', 'answer', 'review'])
        self.assertEqual(result['synthesis']['mode'], 'llm')
        self.assertEqual(result['research']['validation_status'], 'model_reviewed')
        self.assertEqual(result['research']['rounds'], 1)
        run = read_run(result['run_id'])
        self.assertEqual(run['owner_id'], bootstrap_admin_id())
        self.assertEqual(run['status'], 'completed')
        self.assertTrue(run['prompt_hash'])
        self.assertIn('claim:claim1', run['evidence'])
        self.assertEqual(run['result'], result)

    def test_union_synthesis_can_use_both_papers_while_experts_keep_own_evidence(self):
        self.add_nor()
        def synthesize(payload, result):
            keys = ['claim:claim1', 'claim:nor-claim']
            self.assertTrue(all(key in payload['evidence'] for key in keys))
            result['synthesis'] = {'sections': [{'heading': 'Comparison', 'statements': [{
                'kind': 'synthesis', 'text': 'These model estimates use different device scopes.',
                'evidence_ids': keys, 'quotes': {}}]}]}
            return result
        llm = ResearchLLM(answer=synthesize)
        result = self.ask(llm, experts=['dram', 'nor'])
        self.assertEqual(result['synthesis']['mode'], 'llm')
        answer_call=next(payload for payload in llm.calls if operation(payload)=='answer')
        profiles = {p['expert_id']: p['evidence_ids'] for p in answer_call['experts']}
        self.assertIn('claim:claim1', profiles['dram'])
        self.assertNotIn('claim:nor-claim', profiles['dram'])
        self.assertIn('claim:nor-claim', profiles['nor'])
        self.assertNotIn('claim:claim1', profiles['nor'])
        self.assertEqual(llm.operations.count('answer'), 1)

    def test_expert_cannot_cite_another_experts_source(self):
        def leak(payload, result):
            result['answers'][1]['sections'] = copy.deepcopy(result['synthesis']['sections'])
            return result
        result = self.ask(ResearchLLM(answer=leak), experts=['dram', 'nor'])
        self.assertEqual(result['research']['validation_status'], 'withheld')
        self.assertTrue(all(part['mode'] == 'evidence' for part in result['answers']))

    def test_deep_uses_four_calls_and_at_most_six_searches(self):
        llm = ResearchLLM(plan={'queries': ['refresh conditions', 'retention mechanism', 'power model'], 'gaps': ['Unreported conditions']},
                          refine={'queries': ['refresh negative result', 'retention limits'], 'gaps': []})
        result = self.ask(llm, 'deep')
        self.assertEqual(llm.operations, ['plan', 'refine', 'answer', 'review'])
        self.assertEqual(result['research']['rounds'], 6)
        self.assertEqual(result['research']['validation_status'], 'model_reviewed')
        trace = read_run(result['run_id'])['trace']
        self.assertEqual(len(trace['model_calls']), 4)
        self.assertEqual(len(trace['searches']), 6)

    def test_followup_queries_are_casefold_deduplicated(self):
        llm = ResearchLLM(plan={'queries': [' REFRESH 74 ', 'retention', 'RetentION'], 'gaps': []},
                          refine={'queries': ['RETENTION', 'model'], 'gaps': []})
        result = self.ask(llm, 'deep')
        self.assertEqual(result['research']['queries'], ['refresh 74', 'retention', 'model'])
        self.assertEqual(result['research']['rounds'], 3)

    def test_oversized_search_plan_is_not_executed(self):
        llm = ResearchLLM(plan={'queries': ['one', 'two', 'three', 'four'], 'gaps': []})
        result = self.ask(llm, 'deep')
        self.assertEqual(result['research']['queries'], ['refresh 74'])
        self.assertIn('형식', result['synthesis']['notice'])
        self.assertLessEqual(len(llm.calls), 4)

    def test_unsupported_synthesis_is_withheld_without_discarding_supported_perspective(self):
        def review(payload, result):
            for check in result['checks']:
                if check['id'].startswith('synthesis:'):
                    check['verdict'] = 'unsupported'
            return result
        result = self.ask(ResearchLLM(review=review), 'deep')
        self.assertEqual(result['synthesis']['mode'], 'evidence')
        self.assertEqual(result['answers'][0]['mode'], 'llm')
        self.assertEqual(result['research']['validation_status'], 'model_review_partial')
        self.assertTrue(read_run(result['run_id'])['trace']['generated_sections']['synthesis'])
        self.assertEqual(read_run(result['run_id'])['trace']['published_sections']['synthesis'], [])

    def test_review_failure_or_incomplete_review_withholds_generated_answer(self):
        def bad_id(payload, result):
            result['checks'][0]['id'] = []
            return result
        for response in (LLMError('Review service unavailable'), {'checks': []}, bad_id):
            with self.subTest(response=str(response)):
                result = self.ask(ResearchLLM(review=response), 'deep')
                self.assertEqual(result['research']['validation_status'], 'withheld')
                self.assertEqual(result['synthesis']['mode'], 'evidence')
                self.assertTrue(result['claims'])

    def test_invented_numeric_fact_is_withheld_but_explicit_proposal_is_labeled(self):
        def numeric(kind):
            def response(payload, result):
                item = result['synthesis']['sections'][0]['statements'][0]
                item.update(kind=kind, text='Investigate retention after 999999 cycles.')
                return result
            return response
        bad = self.ask(ResearchLLM(answer=numeric('finding')))
        self.assertEqual(bad['research']['validation_status'], 'withheld')
        proposed = self.ask(ResearchLLM(answer=numeric('proposal')))
        self.assertEqual(proposed['synthesis']['mode'], 'llm')
        self.assertIn('제안 실험', proposed['synthesis']['markdown'])

    def test_wiki_only_statements_and_unlinked_wiki_are_rejected(self):
        claim = {'kind': 'claim', 'claim': {'summary': 'Retention was 100 seconds.', 'scope': ''}}
        evidence = {'claim:c': claim, 'wiki:w': {'kind': 'wiki', 'support_ids': ['claim:other']}}
        for ids in (['wiki:w'], ['wiki:w', 'claim:c']):
            with self.subTest(ids=ids), self.assertRaises(WikiError):
                grounded_sections({'sections': [{'heading': 'Conclusion', 'statements': [{
                    'text': 'Retention depends on the conditions.', 'kind': 'synthesis', 'evidence_ids': ids}]}]}, evidence)

    def test_source_correction_during_generation_returns_409_and_saves_original_evidence(self):
        def change_source(payload, result):
            with connect(self.db) as db:
                db.execute("UPDATE claims SET summary='Corrected model interpretation' WHERE id='claim1'")
                db.commit()
            return result
        with self.assertRaises(HTTPException) as caught:
            self.ask(ResearchLLM(answer=change_source))
        self.assertEqual(caught.exception.status_code, 409)
        with connect(self.db) as db:
            run = db.execute('SELECT * FROM answer_runs').fetchone()
        self.assertEqual(run['status'], 'superseded')
        self.assertEqual(json.loads(run['result_json']), {'error': 'evidence_changed'})
        self.assertIn('74', json.loads(run['evidence_json'])['claim:claim1']['claim']['summary'])

    def test_owner_only_trace_and_source_changes_do_not_rewrite_snapshot(self):
        result = self.ask()
        before = read_run(result['run_id'])
        with connect(self.db) as db:
            db.execute("UPDATE claims SET summary='Later correction' WHERE id='claim1'")
            db.commit()
        self.assertEqual(read_run(result['run_id']), before)
        with patch('backend.answer_trace.current_user', return_value={'employee_id': '3000000', 'role': 'admin'}):
            with self.assertRaises(HTTPException) as caught:
                read_run(result['run_id'])
            self.assertEqual(caught.exception.status_code, 404)
        client = TestClient(app)
        try:
            self.assertEqual(client.get('/api/pilot/answer-runs/'+result['run_id']).status_code, 200)
            claim_url = '/api/pilot/answer-runs/'+result['run_id']+'/claims/claim1'
            self.assertIn(claim_url, result['synthesis']['markdown'])
            snapshot = client.get(claim_url)
            self.assertEqual(snapshot.status_code, 200, snapshot.text)
            self.assertEqual(snapshot.json(), before['evidence']['claim:claim1']['claim'])
            self.assertEqual(client.get('/api/pilot/answer-runs/'+result['run_id']+'/claims/missing').status_code, 404)
            with patch('backend.answer_trace.current_user', return_value={'employee_id': '3000000', 'role': 'admin'}):
                self.assertEqual(client.get('/api/pilot/answer-runs/'+result['run_id']).status_code, 404)
                self.assertEqual(client.get(claim_url).status_code, 404)
        finally:
            client.close()

    def test_api_modes_default_and_reject_unknown_values(self):
        self.assertEqual(Question(question='refresh', expert_ids=['dram']).mode, 'deep')
        for mode in ('basic','unbounded'):
            with self.assertRaises(ValidationError):
                Question(question='refresh', expert_ids=['dram'], mode=mode)
        client = TestClient(app)
        try:
            for mode in ('basic','unbounded'):
                self.assertEqual(client.post('/api/pilot/ask', json={'question': 'refresh', 'expert_ids': ['dram'], 'mode': mode}).status_code, 422)
            with patch('backend.wiki_answer.CompanyLLM', return_value=self.no_llm):
                response = client.post('/api/pilot/ask', json={'question': 'refresh', 'expert_ids': ['dram']})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['research']['mode'], 'deep')
        finally:
            client.close()

    def test_unconfigured_deep_mode_does_not_claim_investigation_or_review(self):
        result = self.ask(self.no_llm, 'deep')
        self.assertEqual(result['synthesis']['mode'], 'evidence')
        self.assertEqual(result['research']['validation_status'], 'not_generated')
        self.assertEqual(result['research']['rounds'], 1)
        self.assertIn('수행하지 않았습니다', result['synthesis']['notice'])
        self.assertEqual(read_run(result['run_id'])['trace']['model_calls'], [])

    def test_malformed_model_output_falls_back_to_evidence(self):
        def bad_kind(payload, result):
            result['synthesis']['sections'][0]['statements'][0]['kind'] = []
            return result
        def bad_nested(payload, result):
            result['synthesis']['sections'][0]['statements'][0]['evidence_ids'] = [[]]
            return result
        for value in (None, [], {'synthesis': {'sections': []}, 'answers': []}, {'synthesis': None, 'answers': [{'expert_id': 'dram', 'sections': []}]}, {'synthesis': [], 'answers': [{'expert_id': 'dram', 'sections': []}]}, bad_kind, bad_nested):
            with self.subTest(value=value):
                result = self.ask(ResearchLLM(answer=value))
                self.assertEqual(result['synthesis']['mode'], 'evidence')
                self.assertEqual(result['research']['validation_status'], 'withheld')
                self.assertTrue(result['claims'])


if __name__ == '__main__':
    unittest.main()
