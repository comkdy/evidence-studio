import json
from pathlib import Path
import tempfile
import unittest

from backend.answer_evidence import build_evidence, _diverse_order
from backend.pilot_db import initialize, connect
from backend.wiki_store import WikiError


class MemoryWikiStore:
    def __init__(self, database):
        self.database = database
        self.bodies = {}
        self.invalid = set()

    def read(self, row):
        if row['id'] in self.invalid:
            raise WikiError('Modified Markdown')
        return {}, self.bodies[row['id']]


class AnswerEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'evidence.sqlite3'
        initialize(self.db)
        self.store = MemoryWikiStore(self.db)
        self.catalogue = {}
        self.texts = {
            'p1': 'The measured retention was 100 seconds. Temperature was 90 C. Failure criterion was 1 percent.',
            'p2': 'The measured retention was 80 seconds. Temperature was 85 C. Failure criterion was 2 percent.',
            'p3': 'The simulated retention was 900 seconds. Temperature was 25 C. Not a measurement.',
        }
        with connect(self.db) as db:
            db.execute("INSERT INTO curation_runs VALUES('curation','test','test','payload','now')")
            for index, (pid, text) in enumerate(self.texts.items(), 1):
                db.execute('INSERT INTO papers VALUES(?,?,?,?,?,?)', (pid, 'Paper ' + pid, '', 'test', 'unreviewed', 'now'))
                db.execute('INSERT INTO paper_documents VALUES(?,?,?,?,?,?)', ('doc-' + pid, pid, 'sha-' + pid, 100, 1, 'run-' + pid))
                db.execute('INSERT INTO extraction_runs VALUES(?,?,?,?,?)', ('run-' + pid, 'doc-' + pid, 'test', 'ready', 'now'))
                db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?)', ('page-' + pid, 'run-' + pid, 1, text, 'test', 'ready', len(text)))
                db.execute('INSERT INTO expert_papers VALUES(?,?,?)', ('nor' if pid == 'p3' else 'dram', pid, 'test'))
                quote = text[:text.index('.') + 1]
                db.execute('INSERT INTO evidence_spans VALUES(?,?,?,?,?,?,?)', ('span-' + pid, pid, 'page-' + pid, 0, len(quote), quote, 'test'))
                db.execute('INSERT INTO claims VALUES(?,?,?,?,?,?,?,?)', ('claim-' + pid, pid, 'curation', quote, 'observation', 'tested sample', 'quote_matched', 'human_review_pending'))
                db.execute('INSERT INTO claim_evidence VALUES(?,?,?)', ('claim-' + pid, 'span-' + pid, pid))
            # A linked condition is deliberately outside the claim quotation.
            condition = 'Temperature was 90 C.'
            offset = self.texts['p1'].index(condition)
            db.execute('INSERT INTO evidence_spans VALUES(?,?,?,?,?,?,?)', ('temperature', 'p1', 'page-p1', offset, offset + len(condition), condition, 'test'))
            db.execute('INSERT INTO measurements VALUES(?,?,?,?,?,?,?,?,?,?)', ('measurement', 'p1', 'curation', 'retention', '100 s', '=', 100, 's', 'sample', 'measured'))
            db.execute("INSERT INTO claim_measurements VALUES('claim-p1','measurement','p1','reported-result')")
            db.execute("INSERT INTO measurement_evidence VALUES('measurement','span-p1','p1')")
            db.execute("INSERT INTO condition_values VALUES('condition','measurement','p1','Temperature','90 C','temperature')")
            db.commit()
        self.state = dict(papers={p: dict(hash='hash-' + p) for p in self.texts},
                          experts={'dram': dict(papers=['p1', 'p2'], hash='dram-hash'),
                                   'nor': dict(papers=['p3'], hash='nor-hash')})

    def tearDown(self):
        self.tmp.cleanup()

    def unit(self, kind, ref, *, text='', metadata=None, uid=None, expert_id=None):
        return dict(id=uid or kind + '-' + ref, kind=kind, reference_id=ref, text=text,
                    metadata_json=json.dumps(metadata or {}), expert_id=expert_id)

    def claim(self, pid):
        return self.unit('claim', 'claim-' + pid)

    def passage(self, pid='p1', start=0, end=None, chunk=None):
        text = self.texts[pid][start:end]
        metadata = dict(paper_id=pid, page_id='page-' + pid, document_id='doc-' + pid,
                        page_number=1, offset=start)
        if chunk:
            metadata['chunk_id'] = chunk
        return self.unit('passage', 'passage-' + pid, text=text, metadata=metadata)

    def wiki(self, wiki_id='wiki-one', *, claim_ids=None, paper_ids=None, owner=None, spans=None):
        papers = paper_ids or ['p1']
        refs = dict(paper_ids=papers, claim_ids=claim_ids if claim_ids is not None else ['claim-p1'],
                    source_spans=spans or [], source_hashes={p: self.state['papers'][p]['hash'] for p in papers},
                    dependencies={}, scope_hash=self.state['experts'][owner]['hash'] if owner else '')
        row = dict(id=wiki_id, title='Retention synthesis', path=wiki_id + '.md', revision=2,
                   status='ready', expert_id=owner, refs_json=json.dumps(refs), content_hash='wiki-hash')
        self.catalogue[wiki_id] = row
        self.store.bodies[wiki_id] = 'Retention differs with temperature and the failure criterion.'
        return self.unit('wiki', wiki_id, text=self.store.bodies[wiki_id], metadata={'revision': 2})

    def build(self, ranked, budget=32000, experts=None):
        return build_evidence(ranked, experts or ['dram'], store=self.store, state=self.state,
                              catalogue=self.catalogue, budget=budget, database=self.db)

    def parent(self, text=None):
        text = text or self.texts['p1']
        quote = self.texts['p1'][:self.texts['p1'].index('.') + 1]
        span = dict(page_id='page-p1', page_number=1, start_offset=0, end_offset=len(text),
                    chunk_start=0, chunk_end=len(text))
        with connect(self.db) as db:
            if text != self.texts['p1']:
                db.execute('UPDATE pages SET text=?,char_count=? WHERE id=?', (text, len(text), 'page-p1'))
                self.texts['p1'] = text
            db.execute('INSERT INTO search_contexts VALUES(?,?,?,?,?)', ('parent', 'run-p1', 'Results', text, json.dumps([span])))
            db.execute('INSERT INTO search_chunks VALUES(?,?,?,?,?)', ('child', 'page-p1', 0, len(quote), quote))
            db.execute('INSERT INTO search_chunk_details VALUES(?,?,?,?,?,?)', ('child', 'run-p1', 'parent', 0, 'Results', quote))
            db.execute('INSERT INTO search_chunk_spans VALUES(?,?,?,?,?,?,?,?)', ('child', 0, 'page-p1', 0, len(quote), 0, len(quote), 1))
            db.commit()
        return self.passage(end=len(quote), chunk='child')

    def test_wiki_requires_complete_canonical_support_and_preserves_conditions(self):
        result = self.build([self.wiki()])
        wiki = next(e for e in result['evidence'].values() if e['kind'] == 'wiki')
        self.assertEqual(wiki['support_ids'], ['claim:claim-p1'])
        self.assertEqual(wiki['support_scope'], 'page_dependencies')
        claim = result['evidence'][wiki['support_ids'][0]]['claim']
        self.assertEqual(claim['measurements'][0]['conditions'][0]['raw_value'], '90 C')
        self.assertEqual(claim['measurements'][0]['conditions'][0]['source']['quote'], 'Temperature was 90 C.')
        self.assertEqual(result['wikis'][0]['revision'], 2)

    def test_wiki_budget_never_admits_synthesis_without_all_support(self):
        unit = self.wiki(claim_ids=['claim-p1', 'claim-p2'], paper_ids=['p1', 'p2'])
        result = self.build([unit], budget=800)
        self.assertFalse(any(e['kind'] == 'wiki' for e in result['evidence'].values()))
        self.assertEqual(result['evidence'], {})
        self.assertLessEqual(result['diagnostics']['used_chars'], 800)
        self.assertEqual(result['diagnostics']['dropped_units'][0]['reason'], 'wiki_support_budget')

    def test_missing_or_modified_wiki_support_fails_closed(self):
        no_support = self.wiki('empty-support', claim_ids=[])
        wrong_scope = self.wiki('outside', claim_ids=['claim-p3'], paper_ids=['p3'])
        changed = self.wiki('changed')
        self.store.invalid.add('changed')
        result = self.build([no_support, wrong_scope, changed])
        self.assertEqual(result['evidence'], {})
        self.assertEqual(len(result['diagnostics']['dropped_units']), 3)

    def test_scope_uses_live_active_membership_and_wiki_owner(self):
        owned = self.wiki(owner='nor')
        result = self.build([self.claim('p1'), self.claim('p3'), owned])
        self.assertEqual(set(result['evidence']), {'claim:claim-p1'})
        with connect(self.db) as db:
            db.execute("INSERT INTO expert_deletions VALUES('dram','now')")
            db.commit()
        self.assertEqual(self.build([self.claim('p1')])['evidence'], {})
        union = self.build([self.claim('p3')], experts=['dram', 'nor'])
        self.assertEqual(union['diagnostics']['paper_ids'], ['p3'])

    def test_parent_expansion_adds_conditions_without_changing_source_offsets(self):
        unit = self.parent()
        result = self.build([unit])
        ev = next(iter(result['evidence'].values()))
        self.assertEqual(ev['text'], self.texts['p1'])
        self.assertEqual(ev['context_status'], 'parent_expanded')
        self.assertEqual(ev['offset'], 0)
        self.assertEqual(ev['end_offset'], len(self.texts['p1']))
        self.assertIn('Failure criterion', ev['text'])

    def test_large_parent_keeps_original_span_and_reports_partial_context(self):
        unit = self.parent(self.texts['p1'] + ' More measured conditions.' * 500)
        result = self.build([unit], budget=1500)
        ev = next(iter(result['evidence'].values()))
        self.assertEqual(ev['text'], unit['text'])
        self.assertEqual(ev['context_status'], 'retrieved_span_only')
        self.assertTrue(result['diagnostics']['notices'])
        self.assertLessEqual(result['diagnostics']['used_chars'], 1500)

    def test_parent_spans_preserve_page_boundaries(self):
        unit = self.parent()
        second = 'The following page defines the measurement conditions.'
        with connect(self.db) as db:
            db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?)', ('page-p1-2', 'run-p1', 2, second, 'test', 'ready', len(second)))
            first = self.texts['p1']
            spans = [dict(page_id='page-p1', page_number=1, start_offset=0, end_offset=len(first), chunk_start=0, chunk_end=len(first)),
                     dict(page_id='page-p1-2', page_number=2, start_offset=0, end_offset=len(second), chunk_start=len(first) + 1, chunk_end=len(first) + 1 + len(second))]
            db.execute('UPDATE search_contexts SET text=?,source_spans_json=? WHERE id=?', (first + '\n' + second, json.dumps(spans), 'parent'))
            db.commit()
        result = self.build([unit])
        self.assertEqual([e['page_number'] for e in result['evidence'].values()], [1, 2])
        self.assertEqual([e['offset'] for e in result['evidence'].values()], [0, 0])
        self.assertEqual(list(result['evidence'].values())[1]['text'], second)

    def test_nearby_ranks_keep_second_paper_before_more_first_paper_context(self):
        ranked = [self.claim('p2'), self.passage('p2'), self.passage('p1')]
        result = self.build(ranked, budget=1400)
        self.assertEqual(result['diagnostics']['paper_ids'], ['p1', 'p2'])

    def test_wiki_dependencies_cannot_displace_directly_retrieved_claim(self):
        # A high-ranked overview can reference many unrelated facts. They must
        # not consume the packet before the directly matched retention claim.
        wiki = self.wiki(claim_ids=['claim-p1', 'claim-p2'], paper_ids=['p1', 'p2'])
        direct = self.build([self.claim('p1')])
        budget = direct['diagnostics']['used_chars'] + 200
        result = self.build([wiki, self.claim('p1')], budget=budget)
        self.assertEqual(set(result['evidence']), {'claim:claim-p1'})
        self.assertEqual(result['wikis'], [])
        self.assertEqual(result['diagnostics']['dropped_units'][0]['reason'], 'wiki_support_budget')

    def test_oversized_wiki_does_not_leave_uncited_dependency_claims(self):
        wiki = self.wiki(claim_ids=['claim-p1', 'claim-p2'], paper_ids=['p1', 'p2'])
        both = self.build([self.claim('p1'), self.claim('p2')])
        # All dependencies fit, but the Wiki body does not: accept none of the
        # bundle rather than filling the packet with incidental dependencies.
        result = self.build([wiki], budget=both['diagnostics']['used_chars'])
        self.assertEqual(result['evidence'], {})
        self.assertEqual(result['wikis'], [])

    def test_diversity_does_not_promote_a_distant_low_rank_paper(self):
        candidates = [dict(rank=rank, paper_ids=['p1']) for rank in range(20)]
        candidates.append(dict(rank=50, paper_ids=['p2']))
        order = list(_diverse_order(candidates))
        self.assertEqual([c['rank'] for c in order], list(range(20)) + [50])

    def test_duplicate_source_inside_claim_is_not_repeated_and_stale_text_is_dropped(self):
        quote = self.texts['p1'][:self.texts['p1'].index('.') + 1]
        result = self.build([self.claim('p1'), self.passage(end=len(quote))])
        self.assertEqual(len(result['evidence']), 1)
        stale = self.passage('p2')
        stale['text'] = 'A value invented after indexing.'
        self.assertEqual(self.build([stale])['evidence'], {})

    def test_invalid_parent_mapping_falls_back_to_verified_original(self):
        unit = self.parent()
        with connect(self.db) as db:
            db.execute("UPDATE search_contexts SET text='Different source text' WHERE id='parent'")
            db.commit()
        result = self.build([unit])
        ev = next(iter(result['evidence'].values()))
        self.assertEqual(ev['text'], unit['text'])
        self.assertEqual(ev['context_status'], 'retrieved_span_only')
        self.assertTrue(result['diagnostics']['notices'])

    def test_out_of_bounds_source_and_unbacked_wiki_paper_are_rejected(self):
        text = self.texts['p1']
        oversized = dict(paper_id='p1', page_id='page-p1', start_offset=0,
                         end_offset=len(text) + 200, quote=text)
        invalid = self.wiki('bad-offset', claim_ids=[], spans=[oversized])
        missing = self.wiki('unbacked-paper', claim_ids=['claim-p1'], paper_ids=['p1', 'p2'])
        self.assertEqual(self.build([invalid, missing])['evidence'], {})

    def test_raw_wiki_quote_is_verified_and_database_is_unchanged(self):
        text = self.texts['p2']
        span = dict(paper_id='p2', page_id='page-p2', document_id='doc-p2', page_number=1,
                    start_offset=0, end_offset=len(text), quote=text)
        wiki = self.wiki(claim_ids=[], paper_ids=['p2'], spans=[span])
        before = self.db.read_bytes()
        result = self.build([wiki])
        self.assertEqual(before, self.db.read_bytes())
        raw = next(e for e in result['evidence'].values() if e['kind'] == 'source_text')
        self.assertEqual(raw['text'], text)
        self.assertEqual(raw['context_status'], 'wiki_quote')
        self.assertTrue(result['wikis'])


if __name__ == '__main__':
    unittest.main()
