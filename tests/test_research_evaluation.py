import hashlib
import json
from unittest import TestCase
from unittest.mock import patch

import test_markdown_wiki as wiki_fixtures
import test_pilot as pilot_fixtures
from backend.evaluate_research import evaluate, main, read_only, validate_dataset, _state
from backend.pilot_db import connect
from backend.wiki_engine import reconcile, snapshot


class ResearchEvaluationTests(TestCase):
    curation=pilot_fixtures.PilotTests.curation
    setUp=wiki_fixtures.MarkdownWikiTests.setUp
    tearDown=wiki_fixtures.MarkdownWikiTests.tearDown

    def dataset(self):
        with connect(self.db) as db:
            pid=db.execute('SELECT paper_id FROM claims WHERE id=?',('claim1',)).fetchone()[0]
        return {'dataset_type':'test-demo','cases':[{'id':'fixture-case','question':'refresh 74',
            'expert_ids':['dram'],'expected_paper_ids':[pid],
            'expected_reference_ids':{'structured':['claim:claim1'],'with_wiki':['claim:claim1']}}]}

    def test_read_only_state_matches_live_snapshot_hashes(self):
        reconcile(self.db,self.no_llm)
        with read_only(self.db) as db:
            state=_state(db)
        self.assertEqual(state,snapshot(self.db))

    def test_three_ablations_are_offline_and_do_not_mutate_corpus(self):
        reconcile(self.db,self.no_llm)
        dataset=self.dataset()
        before=hashlib.sha256(self.db.read_bytes()).hexdigest()
        wiki_before={str(p):p.read_bytes() for p in self.store.root.rglob('*.md')}
        with patch('backend.chunking.load_chunk_policy',side_effect=AssertionError('must not load remote config')), \
             patch('backend.ai_settings.load_ai_settings',side_effect=AssertionError('must not load remote config')), \
             patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('network is disabled')):
            report=evaluate(dataset,self.db)
        self.assertEqual(before,hashlib.sha256(self.db.read_bytes()).hexdigest())
        self.assertEqual(wiki_before,{str(p):p.read_bytes() for p in self.store.root.rglob('*.md')})
        self.assertEqual(report['model_requests'],{'embedding':0,'reranker':0,'answer':0})
        self.assertTrue(report['corpus_unchanged'])
        self.assertEqual(report['failures'],[])
        results=report['cases'][0]['variants']
        self.assertEqual(set(results),{'raw_only','structured','with_wiki'})
        self.assertTrue(all(value['metrics']['selected_paper_recall']==1 for value in results.values()))
        self.assertTrue(all(ref.startswith('passage:') for ref in results['raw_only']['selected_reference_ids']))
        self.assertFalse(any(ref.startswith('wiki:') for ref in results['structured']['selected_reference_ids']))
        self.assertEqual(results['with_wiki']['metrics']['grounded_wiki_coverage'],1)

    def test_small_budget_reports_missing_expected_evidence(self):
        reconcile(self.db,self.no_llm)
        report=evaluate(self.dataset(),self.db,budget=2)
        self.assertTrue(report['failures'])
        for result in report['cases'][0]['variants'].values():
            self.assertEqual(result['metrics']['selected_paper_recall'],0)
            self.assertLessEqual(result['metrics']['evidence_chars'],2)

    def test_invalid_demo_ids_are_input_failures_not_quality_scores(self):
        reconcile(self.db,self.no_llm)
        data=self.dataset();data['cases'][0]['expected_paper_ids']=['fake-demo-id']
        report=evaluate(data,self.db)
        self.assertEqual(report['cases'][0]['variants'],{})
        self.assertEqual(report['failures'][0]['kind'],'invalid_case')
        self.assertEqual(report['summary']['raw_only']['evaluated_cases'],0)

    def test_missing_gold_has_null_recall_not_perfect_recall(self):
        reconcile(self.db,self.no_llm)
        data=self.dataset();data['cases'][0].pop('expected_paper_ids');data['cases'][0].pop('expected_reference_ids')
        report=evaluate(data,self.db)
        self.assertIsNone(report['summary']['raw_only']['selected_paper_recall'])
        self.assertIsNone(report['summary']['structured']['selected_reference_recall'])

    def test_allow_models_shares_query_embedding_across_ablations(self):
        reconcile(self.db,self.no_llm)
        with patch('backend.evaluate_research._model_context',return_value=(object(),None)) as configure, \
             patch('backend.evaluate_research._query_vector',return_value=(None,None,1,[])) as query:
            report=evaluate(self.dataset(),self.db,allow_models=True)
        configure.assert_called_once_with(True)
        query.assert_called_once()
        self.assertEqual(report['model_requests']['embedding'],1)
        self.assertEqual(report['model_requests']['answer'],0)

    def test_cli_writes_only_requested_report_and_returns_failure_for_invalid_gold(self):
        reconcile(self.db,self.no_llm)
        data=self.dataset();data['cases'][0]['expected_paper_ids']=['placeholder']
        case_file=self.root/'cases.json';case_file.write_text(json.dumps(data),encoding='utf-8')
        output=self.root/'report.json'
        code=main(['--cases',str(case_file),'--database',str(self.db),'--output',str(output)])
        self.assertEqual(code,1)
        self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['evaluation'],'retrieval_and_evidence_only')

    def test_duplicate_cases_and_untyped_references_are_rejected(self):
        data=self.dataset();data['cases']*=2
        with self.assertRaises(ValueError):validate_dataset(data)
        data=self.dataset();data['cases'][0]['expected_reference_ids']=['claim1']
        with self.assertRaises(ValueError):validate_dataset(data)
