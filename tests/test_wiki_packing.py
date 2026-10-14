"""Wiki call reduction, bounded multi-page evidence, and opt-in light model routing."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import SecretStr
from backend.ai_settings import ModelSettings, load_ai_settings
from backend.llm_client import CompanyLLM, LLMTimeoutError
from backend.pilot_db import initialize
from backend.wiki_budget import WikiBudget, budget_for, extraction_model, page_evidence, source_batches
from backend.wiki_engine import source_page, compile_page, base_refs, synthesize, common_topic
from backend.wiki_policy import load_prompt
from backend.wiki_store import WikiStore, WikiError, digest
import test_markdown_wiki as fixtures


class GroundedModel(fixtures.FakeLLM):
    def __init__(self, name='main', context=16384):
        super().__init__()
        self.identity=name
        self.service=SimpleNamespace(model=name)
        self._wiki_budget=WikiBudget(context,1024,model=name)

    def sections(self,evidence):
        # Cite multiple source pages so tests verify cross-page provenance too.
        keys=list(evidence)[:4]
        return {'sections':[{'heading':'Measured facts','statements':[{
            'text':'Retention was measured under the supplied voltage and temperature conditions.',
            'evidence_ids':keys,'quotes':{k:evidence[k]['text'][:80]
                for k in keys if evidence[k]['kind']=='source_text'}}]}]}


class WikiPackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'wiki.db'
        initialize(self.db)
        self.store=WikiStore(self.db)
        self.model=GroundedModel()
        self.prompt=load_prompt('compile')

    def paper(self, count=10, repeats=1):
        pages=[dict(id=f'page-{i}',document_id='doc',page_number=i+1,status='ready',
            text=''.join(f'Results {i}, experiment {j}: Retention is 10^2 s at 85 C and 1.2 V. Conditions and limitations apply.\n\n' for j in range(repeats)))
               for i in range(count)]
        paper=dict(id='p1',title='DRAM retention evidence',claims=[],notes=[],pages=pages,
                   hash=digest(pages),documents=[])
        self.state={'papers':{'p1':paper},'experts':{}}
        return paper

    def run_source(self,paper,model=None):
        return source_page(self.store,self.state,paper,model or self.model,'test',self.prompt)

    def assert_spans(self,root,paper):
        spans=json.loads(root['refs_json'])['source_spans']
        self.assertTrue(spans)
        pages={p['id']:p for p in paper['pages']}
        for span in spans:
            page=pages[span['page_id']]
            self.assertEqual(span['document_id'],page['document_id'])
            self.assertEqual(span['quote'],page['text'][span['start_offset']:span['end_offset']])

    def test_ten_short_pages_use_one_call_with_original_page_quotes(self):
        paper=self.paper()
        rows=self.run_source(paper)
        self.assertEqual(len(self.model.calls),1)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['kind'],'source')
        self.assertEqual(len(self.model.calls[0]['evidence']),10)
        self.assert_spans(rows[0],paper)
        self.model.calls.clear()
        self.run_source(paper)
        self.assertEqual(self.model.calls,[])

    def test_optional_light_model_extracts_but_main_synthesizes_topics(self):
        light=GroundedModel('light');self.model._wiki_extractor=light
        paper=self.paper();rows=self.run_source(paper)
        self.assertEqual(len(light.calls),1);self.assertEqual(self.model.calls,[])
        self.assertEqual(rows[0]['producer'],'company-llm:light')
        common_topic(self.store,self.state,self.model,rows,'DRAM','test',self.prompt)
        self.assertEqual(len(self.model.calls),1)
        self.assertEqual(self.model.calls[0]['page_kind'],'topic')
        self.assert_spans(rows[0],paper)

    def test_single_main_call_preferred_over_splitting_for_small_light_context(self):
        light=GroundedModel('light',context=7500);self.model._wiki_extractor=light
        self.run_source(self.paper(count=10,repeats=3))
        self.assertEqual(light.calls,[]);self.assertEqual(len(self.model.calls),1)

    def test_long_paper_keeps_every_character_and_offset_with_fewer_calls_than_pages(self):
        paper=self.paper(count=40,repeats=8)
        rows=self.run_source(paper)
        raw=[p for p in self.model.calls if all(e['kind']=='source_text' for e in p['evidence'].values())]
        self.assertGreater(len(raw),1)
        self.assertLess(len(self.model.calls),40)
        self.assertTrue(any(len(p['evidence'])>1 for p in raw))
        by_page={p['id']:[] for p in paper['pages']}
        for system,payload in zip(self.model.systems,self.model.calls):
            self.assertTrue(self.model._wiki_budget.fits(system,payload))
        for payload in raw:
            for e in payload['evidence'].values():by_page[e['page_id']].append((e['offset'],e['text']))
        for page in paper['pages']:
            offset=0
            for start,text in sorted(by_page[page['id']]):
                self.assertEqual(start,offset)
                offset+=len(text)
            self.assertEqual(''.join(t for _,t in sorted(by_page[page['id']])),page['text'])
        self.assert_spans(rows[-1],paper)
        self.model.calls.clear();self.run_source(paper)
        self.assertEqual(self.model.calls,[])

    def test_long_extraction_uses_light_and_final_synthesis_uses_main(self):
        light=GroundedModel('light');self.model._wiki_extractor=light
        paper=self.paper(count=30,repeats=8);root=self.run_source(paper)[-1]
        self.assertGreater(len(light.calls),1)
        self.assertTrue(all(all(e['kind']=='source_text' for e in p['evidence'].values()) for p in light.calls))
        self.assertTrue(all(all(e['kind']=='wiki' for e in p['evidence'].values()) for p in self.model.calls))
        self.assertEqual(root['producer'],'company-llm:main')
        self.assert_spans(root,paper)

    def test_tiny_light_context_falls_back_before_sending_long_paper(self):
        light=GroundedModel('light',context=4096);self.model._wiki_extractor=light
        paper=self.paper(count=30,repeats=8)
        self.run_source(paper)
        self.assertEqual(light.calls,[])
        self.assertGreater(len(self.model.calls),1)

    def test_one_oversized_page_splits_at_sentences_without_cutting_or_repeating_source(self):
        paper=self.paper(count=1)
        paper['pages'][0]['text']=' '.join(f'Experiment {n} measured retention at 85 C with 1.2 V applied.' for n in range(500))
        paper['hash']=digest(paper['pages'])
        self.run_source(paper)
        raw=[e for p in self.model.calls for e in p['evidence'].values() if e['kind']=='source_text']
        self.assertGreater(len(raw),1)
        self.assertEqual(''.join(e['text'] for e in raw),paper['pages'][0]['text'])
        self.assertTrue(all(e['text'].rstrip().endswith('.') for e in raw))

    def test_invalid_light_quotes_never_overwrite_existing_source(self):
        paper=self.paper();old=self.run_source(paper)[-1]
        light=GroundedModel('bad-light');self.model._wiki_extractor=light
        def invalid(system,payload):
            key=next(iter(payload['evidence']))
            return {'sections':[{'heading':'Wrong','statements':[{'text':'Wrong fact',
                'evidence_ids':[key],'quotes':{key:'Invented quote that is absent from this paper.'}}]}]}
        light.complete_json=invalid
        with self.assertRaisesRegex(WikiError,'인용'):
            self.run_source(paper)
        self.assertEqual(self.store.record(old['id'])['content_hash'],old['content_hash'])

    def test_timeout_does_not_retry_on_another_model(self):
        paper=self.paper();light=GroundedModel('light');self.model._wiki_extractor=light
        with patch.object(light,'complete_json',side_effect=LLMTimeoutError('slow')) as call:
            with self.assertRaises(LLMTimeoutError):self.run_source(paper)
        self.assertEqual(call.call_count,1);self.assertEqual(self.model.calls,[])

    def test_partial_failure_reuses_successful_source_batches_on_retry(self):
        paper=self.paper(count=30,repeats=8);original=self.model.complete_json
        calls=[]
        def fail_second(system,payload):
            calls.append(payload)
            if len(calls)==2:raise LLMTimeoutError('slow')
            return original(system,payload)
        with patch.object(self.model,'complete_json',side_effect=fail_second):
            with self.assertRaises(LLMTimeoutError):self.run_source(paper)
        successful=calls[0]['evidence']
        self.model.calls.clear();self.run_source(paper)
        self.assertFalse(any(p['evidence']==successful for p in self.model.calls))

    def test_legacy_source_tree_is_reused_without_forced_rebuild(self):
        paper=self.paper(count=2);parts=[]
        for page in paper['pages']:
            ev={page['id']:dict(kind='source_text',paper_id=paper['id'],page_id=page['id'],document_id='doc',
                               page_number=page['page_number'],offset=0,text=page['text'])}
            pid='part-'+page['id']
            parts.append(compile_page(self.store,self.state,self.model,pid,f'common/sources/parts/{pid}.md',
                pid,ev,base_refs(['p1'],self.state),kind='source_part',prompt=self.prompt))
        root=synthesize(self.store,self.state,self.model,parts,'source-p1','common/sources/p1.md',paper['title'],
                        kind='source',prompt=self.prompt)
        self.model.calls.clear();rows=self.run_source(paper)
        self.assertEqual(self.model.calls,[])
        self.assertEqual(rows[-1]['content_hash'],root['content_hash'])
        self.assertEqual(len(rows),3)

    def test_missing_text_is_explicit_and_does_not_call_model(self):
        paper=self.paper(count=0);root=self.run_source(paper)[-1]
        self.assertEqual(root['status'],'needs_source');self.assertEqual(self.model.calls,[])
        self.assertNotIn('\\n',self.store.read(root)[1])


class WikiBudgetTests(unittest.TestCase):
    def test_complete_request_budget_includes_instructions_previous_wiki_and_json(self):
        budget=WikiBudget(4096,1024)
        self.assertTrue(budget.fits('short',{'evidence':'data'}))
        self.assertFalse(budget.fits('x'*3000,{'evidence':'data'}))
        self.assertFalse(budget.fits('short',{'previous_wiki':'x'*3000}))
        self.assertFalse(budget.fits('short',{'evidence':'한글'*1500}))

    def test_local_tokenizer_disables_truncation_and_padding(self):
        tokenizer={'version':'1.0','truncation':{'direction':'Right','max_length':1,'strategy':'LongestFirst','stride':0},
            'padding':None,'added_tokens':[],'normalizer':None,'pre_tokenizer':{'type':'Whitespace'},
            'post_processor':None,'decoder':None,'model':{'type':'WordLevel','vocab':{'[UNK]':0,'memory':1},'unk_token':'[UNK]'}}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'tokenizer.json';path.write_text(json.dumps(tokenizer),encoding='utf-8')
            budget=WikiBudget(4096,1024,str(path))
            self.assertTrue(budget.fits('system',{'text':'memory '*1000}))
            self.assertFalse(budget.fits('system',{'text':'memory '*3000}))
            self.assertTrue(budget.metadata()['counter'].startswith('local-tokenizer:'))
            with self.assertRaises(WikiError):WikiBudget(4096,1024,str(path.parent/'missing'))

    def test_paragraphs_join_across_pages_without_dropping_multibyte_text(self):
        paper={'id':'p','pages':[dict(id=str(i),document_id='d'+str(i%2),page_number=1,status='ready',
            text=('Retention 조건 10^2 s.\n\n' * 25)) for i in range(4)]}
        budget=WikiBudget(4096,1024)
        batches=source_batches(paper,budget,lambda ev:budget.fits('Rules',{'evidence':ev}))
        self.assertGreater(len(batches),1)
        for page in paper['pages']:
            pieces=sorted((e['offset'],e['text']) for b in batches for e in b.values() if e['page_id']==page['id'])
            self.assertEqual(''.join(text for _,text in pieces),page['text'])
        self.assertTrue(all(budget.fits('Rules',{'evidence':b}) for b in batches))

    def test_oversized_system_fails_before_any_generation(self):
        paper={'id':'p','pages':[dict(id='p1',document_id='d',page_number=1,status='ready',text='Measured retention 10 s.') ]}
        budget=WikiBudget(4096,1024)
        with self.assertRaises(WikiError):source_batches(paper,budget,lambda ev:budget.fits('x'*5000,ev))

    def test_model_settings_and_separate_endpoint_do_not_leak_main_key(self):
        base={'LLM_BASE_URL':'https://main.test/v1','LLM_MODEL':'main','LLM_API_KEY':'main-secret'}
        settings=load_ai_settings(Path('absent-env'),environ=base|{'LLM_LIGHT_MODEL':'small'})
        self.assertEqual(settings.light_llm.api_key.get_secret_value(),'main-secret')
        self.assertEqual(settings.light_llm.base_url,settings.llm.base_url)
        self.assertNotIn('main-secret',repr(settings))
        with self.assertRaisesRegex(ValueError,'LLM_LIGHT_API_KEY'):
            load_ai_settings(Path('absent-env'),environ=base|{'LLM_LIGHT_MODEL':'small','LLM_LIGHT_BASE_URL':'https://other.test/v1'})
        settings=load_ai_settings(Path('absent-env'),environ=base|{'LLM_LIGHT_MODEL':'small','LLM_LIGHT_BASE_URL':'https://other.test/v1',
            'LLM_LIGHT_API_KEY':'light-secret','LLM_LIGHT_CONTEXT_TOKENS':'8192','LLM_CONTEXT_TOKENS':'32768'})
        self.assertEqual(settings.llm.context_tokens,32768);self.assertEqual(settings.light_llm.context_tokens,8192)
        with patch('backend.wiki_budget.load_ai_settings',return_value=settings):
            client=CompanyLLM(settings.llm);light=extraction_model(client)
            self.assertEqual(light.service.model,'small')
            self.assertEqual(budget_for(light).context_tokens,8192)

    def test_invalid_context_and_output_limits_fail_without_echoing_values(self):
        for name,value in [('LLM_CONTEXT_TOKENS','secret-invalid'),('LLM_CONTEXT_TOKENS','1024'),('WIKI_OUTPUT_TOKENS','16000')]:
            with self.subTest(name=name),self.assertRaises(ValueError) as caught:
                load_ai_settings(Path('absent-env'),environ={name:value})
            self.assertNotIn('secret-invalid',str(caught.exception))

    def test_wiki_output_reserve_is_sent_to_chat_completions(self):
        response=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"sections":[]}'}}]}).encode()
        with patch('backend.llm_client.build_opener') as opener:
            opener.return_value.open.return_value=io.BytesIO(response)
            client=CompanyLLM(ModelSettings('https://test.invalid/v1','light',SecretStr('fake')))
            client.complete_json('system',{'evidence':{}},max_output_tokens=1024)
            body=json.loads(opener.return_value.open.call_args.args[0].data)
            self.assertEqual(body['max_tokens'],1024)
            self.assertEqual(body['model'],'light')
