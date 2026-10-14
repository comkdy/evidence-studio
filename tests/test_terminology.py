import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from fastapi.testclient import TestClient
from backend.main import app
from backend.pilot_db import connect,initialize
from backend.terminology import snapshot,query_rules,prompt_rules,prompt_text,pure_unit,conjunctive_groups
from backend.wiki_retrieval import keywords,retrieve
from backend.sso_adapter import EmployeeProfile
import test_markdown_wiki as fixtures
import test_pilot as pilot_fixtures


class TerminologyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Path(self.tmp.name)/'test.sqlite3'
        self.env=patch.dict(os.environ,{'EVIDENCE_DB_PATH':str(self.db),'AUTH_MODE':'local_admin'})
        self.env.start();initialize(self.db)
        self.client=TestClient(app,headers={'Origin':'http://testserver'});self.url='/api/pilot/terminology'

    def tearDown(self):
        self.client.close();self.env.stop();self.tmp.cleanup()

    def create(self,**changes):
        payload=dict(id=str(uuid4()),preferred='threshold voltage',aliases=['문턱 전압','Vth'],status='approved')|changes
        response=self.client.post(self.url,json=payload)
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def update(self,term,**changes):
        return self.client.put(self.url+'/'+term['id'],json=term|changes)

    def test_seed_archive_and_restart_never_resurrect(self):
        terms=snapshot(self.db)['terms'];term=next(t for t in terms if t['id']=='retention-time')
        self.assertEqual(self.update(term,status='archived').status_code,200)
        initialize(self.db)
        self.assertEqual(len(snapshot(self.db)['terms']),len(terms)-1)
        self.assertNotIn('retention time',[t['preferred'] for t in query_rules('보존 시간',database=self.db)['matches']])
        history=self.client.get(self.url+'/'+term['id']).json()['history']
        self.assertEqual([h['snapshot']['status'] for h in history],['archived','approved'])

    def test_query_reads_edits_immediately_and_preserves_source(self):
        term=self.create();original='Vth / 문턱 전압 / avthx / HfO₂ / 10² V'
        result=query_rules(original,database=self.db)
        self.assertIn('threshold voltage',result['normalized']);self.assertIn('avthx',result['normalized'])
        self.assertEqual(original,'Vth / 문턱 전압 / avthx / HfO₂ / 10² V')
        self.assertIn('voltage',keywords('Vth',self.db))
        self.assertEqual(self.update(term,status='archived').status_code,200)
        self.assertNotIn('voltage',keywords('Vth',self.db))

    def test_legacy_metadata_is_preserved_but_not_used_by_search_or_prompts(self):
        term=self.create()
        with connect(self.db) as db:
            db.execute('UPDATE terminology_entries SET related_json=?,scope=?,note=? WHERE id=?',
                (json.dumps(['retention','endurance']),'legacy-domain','legacy-guidance',term['id']));db.commit()
        glossary=snapshot(self.db)
        result=query_rules('문턱 전압',glossary)
        self.assertNotIn('retention',result['keywords']);self.assertEqual(result['normalized'],'threshold voltage')
        self.assertNotIn('retention',conjunctive_groups('문턱 전압',glossary)[0])
        self.assertNotIn(term['id'],[t['id'] for t in query_rules('retention',glossary)['matches']])
        rules=prompt_rules('문턱 전압',glossary)
        for field in ('related','scope','note'):self.assertNotIn(field,rules[0])
        self.assertNotIn('endurance',prompt_text('policy',rules))
        self.assertEqual(self.client.get(self.url,params={'q':'legacy-domain'}).json()['total'],0)
        updated=self.update(term,aliases=['Vth','문턱 전압','임계 전압']).json()
        self.assertEqual(updated['scope'],'legacy-domain');self.assertEqual(updated['related'],['retention','endurance'])
        self.assertEqual(self.update(updated).json()['revision'],updated['revision'])

    def test_typography_and_units(self):
        for unit in ('V','cm⁻²','cm²/Vs','Ω·cm','TOPS/W','%','°C'):
            self.assertTrue(pure_unit(unit),unit)
            self.assertEqual(self.client.post(self.url,json={'id':str(uuid4()),'preferred':unit}).status_code,422)
        for concept in ('HfO₂','MoS2','Ga2O3','Vth','T1C','RRAM','KV cache'):
            self.assertFalse(pure_unit(concept),concept)
        term=self.create(preferred='HfO₂',aliases=['HfO2','산화 하프늄'])
        self.assertEqual(term['preferred'],'HfO₂');self.assertIn('HfO2',term['aliases'])
        self.assertEqual(query_rules('HfO2',database=self.db)['matches'][0]['preferred'],'HfO₂')

    def test_conflict_noop_stale_and_audit(self):
        term=self.create()
        duplicate=self.client.post(self.url,json={'id':str(uuid4()),'preferred':'another concept','aliases':['VTH'],'status':'approved'})
        self.assertEqual(duplicate.status_code,409)
        revision=snapshot(self.db)['revision']
        self.assertEqual(self.update(term).json()['revision'],1)
        self.assertEqual(snapshot(self.db)['revision'],revision)
        updated=self.update(term,aliases=term['aliases']+['임계 전압']).json()
        self.assertEqual(updated['revision'],2)
        self.assertEqual(self.update(term,aliases=['old']).status_code,409)
        self.assertEqual(len(self.client.get(self.url+'/'+term['id']).json()['history']),2)
        self.assertEqual(self.client.post(self.url,json=updated).status_code,201)

    def test_approval_and_role_enforcement(self):
        self.client.cookies.set('sso','verified')
        with patch.dict(os.environ,{'AUTH_MODE':'sso','SSO_COOKIE_NAME':'sso'}),patch('backend.sso_adapter.verify_sso_cookie',return_value='3000001'),patch('backend.sso_adapter.fetch_employee_profile',return_value=EmployeeProfile('3000001','연구원','연구팀')):
            draft=self.create(status='draft')
            self.assertNotIn('voltage',keywords('Vth',self.db))
            self.assertEqual(self.update(draft,status='approved').status_code,403)
            self.assertEqual(self.client.post(self.url,json={'id':str(uuid4()),'preferred':'forbidden','status':'approved'}).status_code,403)
            with connect(self.db) as db:db.execute("UPDATE users SET role='superuser' WHERE employee_id='3000001'");db.commit()
            self.assertEqual(self.update(draft,status='approved').status_code,200)
            self.assertIn('voltage',keywords('Vth',self.db))

    def test_list_preview_filters_and_bounded_prompts(self):
        self.create()
        listing=self.client.get(self.url,params={'q':'문턱','limit':1}).json()
        self.assertEqual(listing['total'],1)
        self.assertEqual(self.client.post(self.url+'/preview',json={'text':'문턱 전압'}).json()['normalized'],'threshold voltage')
        glossary=snapshot(self.db)
        rules=prompt_rules('보존 시간과 VDD',glossary,max_terms=1)
        self.assertEqual(len(rules),1);self.assertEqual(rules[0]['preferred'],'retention time')
        self.assertNotIn('retention', [r['preferred'] for r in rules])
        self.assertEqual(prompt_rules('unrelated-token',glossary),[])

    def test_search_keeps_each_concept_and_original_abbreviation(self):
        self.create()
        groups=conjunctive_groups('Vth 전력 90')
        self.assertEqual(len(groups),3)
        self.assertIn('vth',groups[0]);self.assertIn('threshold voltage',groups[0])
        self.assertIn('power',groups[1]);self.assertEqual(groups[2],['90'])

    def test_quotes_in_aliases_cannot_break_fts(self):
        from backend.pilot_api import search
        self.create(aliases=['quoted "parameter"'])
        self.assertEqual(search('quoted "parameter"',expert='',limit=8)['hits'],[])


class TerminologyIntegrationTests(unittest.TestCase):
    curation=pilot_fixtures.PilotTests.curation
    setUp=fixtures.MarkdownWikiTests.setUp
    tearDown=fixtures.MarkdownWikiTests.tearDown

    def test_compile_answer_lint_read_rules_keep_evidence_and_do_not_reindex_on_edit(self):
        from backend.wiki_engine import reconcile
        from backend.wiki_lint import run_lint
        from backend.wiki_api import Question
        llm=fixtures.FakeLLM();reconcile(self.db,llm)
        self.assertTrue(any('Approved terminology' in s for s in llm.systems))
        self.assertTrue(all('원문 인용' in s for s in llm.systems if 'Approved terminology' in s))
        with connect(self.db) as db:
            before={t:[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in ('claims','measurements','pages','search_chunks','retrieval_units','wiki_jobs')}
        client=TestClient(app,headers={'Origin':'http://testserver'})
        term=next(t for t in snapshot(self.db)['terms'] if t['id']=='refresh')
        response=client.put('/api/pilot/terminology/refresh',json=term|{'aliases':term['aliases']+['갱신실험용어']})
        self.assertEqual(response.status_code,200,response.text)
        with connect(self.db) as db:
            after={t:[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in before}
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(before,after)
        self.assertTrue(retrieve('갱신실험용어',['dram'],self.db)[0])
        self.assertEqual(retrieve('갱신실험용어',['nor'],self.db)[0],[])
        llm.calls.clear();llm.systems.clear()
        from backend.wiki_answer import answer as research_answer
        result=research_answer(Question(question='리프레시 전력 감소',expert_ids=['dram']),llm)
        self.assertTrue(any('Approved terminology' in s for s in llm.systems))
        with connect(self.db) as db:
            trace=json.loads(db.execute('SELECT trace_json FROM answer_runs WHERE id=?',(result['run_id'],)).fetchone()[0])
        self.assertEqual(trace['terminology_revision'],snapshot(self.db)['revision'])
        self.assertTrue(trace['model_calls'][0]['terminology'])
        llm.systems.clear();run_lint(self.db,llm=llm)
        self.assertTrue(any('Approved terminology' in s for s in llm.systems))
        client.close()
