"""Research retrieval and complete, stable topic coverage (temporary data only)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_markdown_wiki as wiki_fixtures
import test_pilot as pilot_fixtures
from backend.pilot_db import connect, initialize
from backend.wiki_engine import base_refs, common_topic, reconcile
from backend.wiki_policy import load_prompt
from backend.wiki_retrieval import keywords, retrieve
from backend.wiki_store import WikiStore, digest


class ResearchRetrievalTests(unittest.TestCase):
    curation=pilot_fixtures.PilotTests.curation
    setUp=wiki_fixtures.MarkdownWikiTests.setUp
    tearDown=wiki_fixtures.MarkdownWikiTests.tearDown

    def test_shared_korean_glossary_retrieves_english_paper_without_vectors(self):
        reconcile(self.db,self.no_llm)
        terms=keywords('보존 시간과 리프레시 간격, 신뢰성을 비교해줘')
        self.assertIn('retention',terms)
        self.assertIn('refresh',terms)
        self.assertIn('reliability',terms)
        ranked,notices=retrieve('보존 시간', ['dram'],self.db)
        self.assertTrue(ranked)
        self.assertTrue(any('Embedding이 설정되지' in item for item in notices))
        self.assertEqual(retrieve('보존 시간',['nor'],self.db)[0],[])

    def test_domain_terms_preserve_english_abbreviations_and_word_boundaries(self):
        terms=keywords('강유전 분극의 산소 공공과 VDD')
        self.assertTrue({'ferroelectric','polarization','oxygen','vacancy','vdd','supply','voltage'}.issubset(terms))
        self.assertNotIn('voltage',keywords('avddx'))

    def test_empty_scope_never_searches_all_experts(self):
        with patch('backend.wiki_retrieval.connect',side_effect=AssertionError('unscoped DB read')):
            ranked,notices=retrieve('retention',[],self.db)
        self.assertEqual(ranked,[])
        self.assertTrue(notices)

    def test_missing_fts_reports_unavailable_index_without_claiming_evidence(self):
        reconcile(self.db,self.no_llm)
        with connect(self.db) as db:
            db.execute('DROP TABLE retrieval_fts');db.commit()
        ranked,notices=retrieve('retention',['dram'],self.db)
        self.assertEqual(ranked,[])
        self.assertTrue(any('키워드 색인이 준비되지' in item for item in notices))
        self.assertTrue(any('관련 근거를 찾지 못' in item for item in notices))

    def test_source_discloses_selected_coverage_and_compile_receives_limit(self):
        llm=wiki_fixtures.FakeLLM()
        reconcile(self.db,llm)
        source=next(row for row in self.store.rows() if row['kind']=='source')
        body=self.store.read(source)[1]
        self.assertIn('선별된 주장',body)
        self.assertIn('논문 전체를 빠짐없이 분석한 기록은 아니며',body)
        self.assertTrue(llm.calls)
        self.assertTrue(all(not call['coverage']['complete_paper_review'] for call in llm.calls))


class TopicCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/'topics.sqlite3'
        initialize(self.db)
        self.store=WikiStore(self.db)
        self.state={'papers':{},'experts':{}}
        self.llm=wiki_fixtures.FakeLLM()
        self.prompt=load_prompt('compile')

    def tearDown(self):
        self.tmp.cleanup()

    def sources(self,ids):
        rows=[]
        for pid in ids:
            self.state['papers'][pid]={'id':pid,'hash':digest(pid),'claims':[]}
            rows.append(self.store.publish('source-'+pid,'common/sources/'+pid+'.md',pid,
                '# '+pid+'\n\nSelected evidence.',base_refs([pid],self.state),digest(pid),kind='source'))
        return rows

    def compile(self,rows):
        return common_topic(self.store,self.state,self.llm,rows,'Retention','test',self.prompt)

    def test_six_to_seven_keeps_root_and_previous_synthesis(self):
        sources=self.sources(['p'+str(n) for n in range(6)])
        parts,root=self.compile(sources)
        self.assertEqual(parts,[])
        self.store.publish(root['id'],root['path'],root['title'],'# Retention\n\nReviewed synthesis.',
            json.loads(root['refs_json']),root['input_hash'],producer='assistant-synthesis',adopt=True)
        self.llm.calls.clear()
        _,unchanged=self.compile(sources)
        self.assertEqual(self.llm.calls,[])
        self.assertIn('Reviewed synthesis',self.store.read(unchanged)[1])
        parts,grown=self.compile(sources+self.sources(['p6']))
        self.assertEqual(root['id'],grown['id'])
        self.assertEqual(len(json.loads(grown['refs_json'])['paper_ids']),7)
        self.assertEqual(len(parts),2)
        self.assertIn('Reviewed synthesis',self.llm.calls[-1]['previous_wiki'])
        with connect(self.db) as db:
            self.assertGreaterEqual(db.execute('SELECT count(*) FROM wiki_revisions WHERE page_id=?',(root['id'],)).fetchone()[0],3)

    def test_all_sources_reach_root_and_each_synthesis_has_bounded_fanout(self):
        sources=self.sources(['p'+str(n).zfill(3) for n in range(37)])
        parts,root=self.compile(sources)
        self.assertEqual(len(json.loads(root['refs_json'])['paper_ids']),37)
        self.assertTrue(all(len(call['evidence'])<=6 for call in self.llm.calls))
        self.assertTrue(all(part['kind']=='topic_part' for part in parts))
        self.assertEqual(len(json.loads(root['refs_json'])['topic_source_order']),37)

    def test_appending_lexically_earlier_paper_keeps_full_groups_unchanged(self):
        sources=self.sources(['p'+str(n).zfill(3) for n in range(13)])
        parts,root=self.compile(sources)
        full={row['id']:row['content_hash'] for row in parts if len(json.loads(row['refs_json'])['paper_ids'])==6}
        self.llm.calls.clear()
        more,grown=self.compile(self.sources(['a-new'])+list(reversed(sources)))
        current={row['id']:row['content_hash'] for row in more}
        self.assertTrue(all(current.get(key)==value for key,value in full.items()))
        self.assertEqual(root['id'],grown['id'])
        self.assertEqual(json.loads(grown['refs_json'])['topic_source_order'][-1],'source-a-new')
        self.assertEqual(len(self.llm.calls),2)  # changed final group plus root

    def test_legacy_extra_topic_is_archived_without_removing_its_file(self):
        # Reconciliation is exercised with isolated state and no real corpus/model.
        sources=self.sources(['p'+str(n) for n in range(7)])
        for paper in self.state['papers'].values():
            paper.update(title=paper['id'],notes=[],documents=[],pages=[],tags=['Retention'])
        old=self.store.publish('common-'+digest('Retention')[:12]+'-1-0',
            'common/topics/legacy.md','Retention legacy','# A preserved editorial note',
            base_refs(['p0'],self.state),'legacy',producer='assistant-synthesis')
        content=(self.store.root/old['path']).read_bytes()
        by_paper={json.loads(row['refs_json'])['paper_ids'][0]:row for row in sources}
        with patch('backend.wiki_engine.snapshot',return_value=self.state), \
             patch('backend.wiki_engine.source_page',side_effect=lambda store,state,paper,*args:[by_paper[paper['id']]]), \
             patch('backend.wiki_retrieval.reindex',return_value={}):
            reconcile(self.db,self.llm)
        self.assertEqual(self.store.record(old['id'])['status'],'archived')
        self.assertEqual((self.store.root/old['path']).read_bytes(),content)

    def test_specialization_keeps_explicit_reference_to_legacy_topic(self):
        sources=self.sources(['p'+str(n) for n in range(7)])
        for paper in self.state['papers'].values():
            paper.update(title=paper['id'],notes=[],documents=[],pages=[],tags=['Retention'])
        self.state['experts']['dram']={'id':'dram','name':'DRAM','overview':'Retention',
            'papers':list(self.state['papers']),'hash':'scope'}
        old=self.store.publish('common-'+digest('Retention')[:12]+'-1-0',
            'common/topics/legacy.md','Retention legacy','# A preserved editorial note',
            base_refs(['p0'],self.state),'legacy',producer='assistant-synthesis')
        with connect(self.db) as db:
            db.execute('INSERT INTO wiki_topic_requests VALUES(?,?,?,?,?)',
                ('requested-note','dram','Experimental considerations',json.dumps([old['id']]),'2026-10-13'))
            db.commit()
        by_paper={json.loads(row['refs_json'])['paper_ids'][0]:row for row in sources}
        with patch('backend.wiki_engine.snapshot',return_value=self.state), \
             patch('backend.wiki_engine.source_page',side_effect=lambda store,state,paper,*args:[by_paper[paper['id']]]), \
             patch('backend.wiki_retrieval.reindex',return_value={}):
            reconcile(self.db,self.llm)
        self.assertEqual(self.store.record(old['id'])['status'],'ready')
        note=self.store.record('requested-note')
        self.assertEqual(note['status'],'ready')
        self.assertIn(old['id'],json.loads(note['refs_json'])['dependencies'])


if __name__=='__main__':
    unittest.main()
