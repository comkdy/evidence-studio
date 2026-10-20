"""Diagnostic runner isolation, live adapters, accounting and report contracts."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from backend.diagnostics import ROOT, DEFAULTS, child_environment, main, run_checks, selected_steps, output_directory
from backend.diagnostic_report import write_report


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.received.append((self.path,body,self.headers.get('Authorization')))
        if self.server.delay:time.sleep(self.server.delay)
        if self.server.error:
            self.send_response(401);self.end_headers();self.wfile.write(b'private-provider-body');return
        if self.path=='/embedding' and 'input' in body:
            vector = [x/128-1 for x in hashlib.sha256(body['input'].encode()).digest()[:16]]
            result = {'data':[{'embedding':vector}]}
        elif self.path=='/embedding' and 'documents' in body:
            result = {'results':[{'index':i,'relevance_score':.9 if 'dram' in text.lower() else .1} for i,text in enumerate(body['documents'])]}
        else:
            payload = json.loads(body['messages'][1]['content'])
            def sections(evidence):
                key = next((k for k,e in evidence.items() if e['kind']=='claim'), next(iter(evidence)))
                ev = evidence[key]
                quote = {key:ev['text'][:80]} if ev['kind']=='source_text' else {}
                return {'sections':[{'heading':'Simulation result','statements':[{'kind':'finding',
                    'text':'The DRAM simulation estimated 25 percent refresh power reduction at 85 C.',
                    'evidence_ids':[key],'quotes':quote}]}]}
            if 'text' in payload:content = dict(parameter='Refresh power reduction',value=25,unit='%',temperature_c=85,evidence_type='simulation estimate')
            elif 'statements' in payload:content = {'checks':[{'id':s['id'],'verdict':'supported'} for s in payload['statements']]}
            elif 'queries' in payload:content = {'queries':[],'gaps':[]}
            elif 'experts' in payload and payload.get('evidence'):
                evidence = payload['evidence']
                content = {'synthesis':sections(evidence),'answers':[{'expert_id':e['expert_id'],**sections({key:evidence[key] for key in e['evidence_ids']})} for e in payload['experts']]}
            elif 'page_kind' in payload:content = sections(payload['evidence'])
            else:content = {'queries':[],'gaps':[]}
            result = {'choices':[{'finish_reason':'stop','message':{'content':'{broken' if self.server.invalid else json.dumps(content)}}],
                      'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}}
        raw = json.dumps(result).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
        try:self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass


@contextmanager
def server(**options):
    http = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    http.received=[];http.delay=0;http.error=False;http.invalid=False
    for key,value in options.items():setattr(http,key,value)
    thread = threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:yield http
    finally:http.shutdown();http.server_close();thread.join()


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ,DEFAULTS)
        self.env.start()

    def tearDown(self):
        self.env.stop();self.tmp.cleanup()

    def settings(self,http):
        url = f'http://127.0.0.1:{http.server_port}'
        # Explicit child-process environment has the same precedence as the app.
        return dict(LLM_BASE_URL=url,LLM_BASE_MODEL='diagnostic-main',LLM_API_KEY='private-model-key',
            LLM_LIGHT_MODEL='diagnostic-small',EMBEDDING_BASE_URL=url+'/embedding',EMBEDDING_MODEL='diagnostic-vector',
            EMBEDDING_API_KEY='private-vector-key',RERANKER_MODEL='diagnostic-rank')

    def run_steps(self,steps,**kwargs):
        directory,code = run_checks(steps,output=self.root/'reports',**kwargs)
        report = json.loads((directory/'report.json').read_text(encoding='utf-8'))
        return directory,code,report

    def test_selection_and_output_guard(self):
        self.assertEqual(selected_steps('llm,embedding,llm'),['llm','embedding'])
        with self.assertRaises(ValueError):selected_steps('unknown')
        for name in ('data','wiki','raw','backend'):
            with self.subTest(name=name),self.assertRaises(ValueError):output_directory(ROOT/name/'reports')

    def test_env_file_precedence_and_step_specific_settings(self):
        env = self.root/'company.env'
        env.write_text("LLM_BASE_MODEL=file-model\nLLM_API_KEY='file-${literal}'\nAUTH_MODE=sso\nEVIDENCE_DB_PATH=wrong.sqlite3\n",encoding='utf-8')
        with patch.dict(os.environ,{},clear=True):
            values = child_environment(env,self.root/'isolated',live=True,step='llm')
        self.assertEqual(values['LLM_BASE_MODEL'],'file-model')
        self.assertEqual(values['LLM_API_KEY'],'file-${literal}')
        self.assertEqual(values['AUTH_MODE'],'local_admin')
        self.assertEqual(values['EVIDENCE_DB_PATH'],str(self.root/'isolated'/'diagnostic.sqlite3'))
        with patch.dict(os.environ,{'LLM_BASE_MODEL':'process-model'}):
            values = child_environment(env,self.root/'isolated',live=True,step='llm')
        self.assertEqual(values['LLM_BASE_MODEL'],'process-model')
        self.assertEqual(child_environment(env,self.root,live=False)['LLM_API_KEY'],'')

    def test_offline_checks_ignore_production_database_and_live_config(self):
        production = self.root/'production';production.mkdir()
        sentinel = production/'production.sqlite3';sentinel.write_bytes(b'production sentinel')
        wiki = self.root/'production-wiki.md';wiki.write_text('keep this wiki',encoding='utf-8')
        with server() as http,patch.dict(os.environ,{**self.settings(http),'EVIDENCE_DB_PATH':str(sentinel)}):
            directory,code,report = self.run_steps(['ingest','curation','chunking','llm'])
            self.assertEqual(http.received,[])
        self.assertEqual(code,2)
        self.assertEqual([r['status'] for r in report['results']],['passed','passed','passed','skipped'])
        self.assertEqual(sentinel.read_bytes(),b'production sentinel')
        self.assertEqual(wiki.read_text(encoding='utf-8'),'keep this wiki')
        self.assertTrue((directory/'report.html').is_file())

    def test_real_http_adapters_and_full_workflows_with_synthetic_server(self):
        with server() as http,patch.dict(os.environ,self.settings(http)):
            directory,code,report = self.run_steps(['llm','llm-light','embedding','reranker','chunking','wiki','answer'],live=True)
            self.assertEqual(code,0,json.dumps(report,ensure_ascii=False))
            self.assertTrue(http.received)
            rerank_headers = [header for path,body,header in http.received if body['model']=='diagnostic-rank']
            self.assertTrue(rerank_headers)
            self.assertTrue(all(h=='Bearer private-vector-key' for h in rerank_headers))
            for path,body,header in http.received:
                if body['model'] in ('diagnostic-main','diagnostic-small'):
                    self.assertEqual(path,'/chat/completions')
                    self.assertEqual(header,'Bearer private-model-key')
                else:
                    self.assertEqual(path,'/embedding')
                    self.assertEqual(header,'Bearer private-vector-key')
            text = (directory/'report.json').read_text(encoding='utf-8')+(directory/'report.html').read_text(encoding='utf-8')
            for secret in ('private-model-key','private-vector-key',f'127.0.0.1:{http.server_port}','private-provider-body'):
                self.assertNotIn(secret,text)
        calls = [call for r in report['results'] for call in r['calls']]
        self.assertTrue(any(c['service']=='llm-light' for c in calls))
        self.assertTrue(all(c['total_tokens']==120 for c in calls if c['service'].startswith('llm')))
        self.assertTrue(all(c['total_tokens'] is None for c in calls if c['service']=='embedding'))

    def test_invalid_json_is_failed_but_api_usage_is_recorded(self):
        with server(invalid=True) as http,patch.dict(os.environ,self.settings(http)):
            _,code,report = self.run_steps(['llm'],live=True)
        self.assertEqual(code,1)
        self.assertEqual(report['results'][0]['calls'][0]['total_tokens'],120)

    def test_reranker_only_uses_shared_endpoint_and_key(self):
        with server() as http,patch.dict(os.environ,self.settings(http)):
            _,code,report=self.run_steps(['reranker'],live=True)
            self.assertEqual(code,0,report)
            self.assertTrue(http.received)
            self.assertTrue(all(path=='/embedding' and body['model']=='diagnostic-rank'
                                and header=='Bearer private-vector-key' for path,body,header in http.received))

    def test_legacy_model_alias_is_preserved_by_live_diagnostics_and_cleared_offline(self):
        env=self.root/'legacy.env'
        env.write_text('LLM_MODEL=old-model\nLLM_API_KEY=old-key\n',encoding='utf-8')
        with patch.dict(os.environ,{},clear=True):
            values=child_environment(env,self.root/'isolated',live=True,step='llm')
            self.assertEqual(values['LLM_BASE_MODEL'],'old-model')
            self.assertEqual(child_environment(env,self.root,live=False)['LLM_BASE_MODEL'],'')
        with patch.dict(os.environ,{'LLM_MODEL':'process-old'},clear=True):
            self.assertEqual(child_environment(env,self.root,live=True,step='llm')['LLM_BASE_MODEL'],'process-old')

    def test_auth_error_does_not_leak_provider_body_or_invent_tokens(self):
        with server(error=True) as http,patch.dict(os.environ,self.settings(http)):
            directory,code,report = self.run_steps(['llm'],live=True)
        self.assertEqual(code,1)
        call = report['results'][0]['calls'][0]
        self.assertEqual(call['http_status'],401)
        self.assertIsNone(call['total_tokens'])
        self.assertNotIn('private-provider-body',(directory/'report.html').read_text(encoding='utf-8'))

    def test_timeout_leaves_partial_html_and_no_automatic_generation_retry(self):
        with server(delay=2) as http,patch.dict(os.environ,self.settings(http)):
            directory,code,report = self.run_steps(['llm'],live=True,stage_timeout=1)
            self.assertLessEqual(len(http.received),1)
        self.assertEqual(code,1)
        self.assertIn('시간 한도',report['results'][0]['summary'])
        self.assertTrue((directory/'report.html').is_file())

    def test_unconfigured_optional_model_is_skipped(self):
        _,code,report = self.run_steps(['llm-light'],live=True)
        self.assertEqual(code,2)
        self.assertEqual(report['results'][0]['calls'],[])

    def test_html_escapes_untrusted_model_names_and_is_self_contained(self):
        report = dict(run_id='test',created_at='now',live=True,results=[dict(step='llm',title='LLM',status='failed',
            calls=[dict(service='llm',model='<script>alert(1)</script>',status='failed',elapsed_ms=1,input_tokens=None,output_tokens=None,total_tokens=None)],checks=[])])
        write_report(self.root,report)
        text = (self.root/'report.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>',text)
        self.assertIn('&lt;script&gt;',text)
        self.assertIn('미제공',text)
        self.assertIn("default-src 'none'",text)

    def test_child_guard_blocks_writes_and_db_connections_outside_sandbox(self):
        isolated = self.root/'isolated';isolated.mkdir()
        outside = self.root/'outside.txt';outside.write_text('preserve')
        script = ('from backend.diagnostic_safety import install_guard\nimport sqlite3\nfrom pathlib import Path\n'
                  f'install_guard({str(isolated)!r})\n'
                  f'for action in [lambda: Path({str(outside)!r}).write_text("wrong"),lambda:sqlite3.connect({str(self.root/"outside.sqlite3")!r})]:\n'
                  ' try:action()\n except PermissionError:pass\n else:raise AssertionError("guard did not block")\n')
        result = subprocess.run([sys.executable,'-B','-c',script],cwd=ROOT,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr.decode())
        self.assertEqual(outside.read_text(),'preserve')
        self.assertFalse((self.root/'outside.sqlite3').exists())

    def test_retry_selects_only_failed_steps_without_implicitly_enabling_live(self):
        path = self.root/'previous.json'
        path.write_text(json.dumps({'results':[{'step':'llm','status':'failed'},{'step':'ingest','status':'passed'}]}),encoding='utf-8')
        with patch.object(sys,'argv',['diagnostics','--retry-from',str(path)]),patch('backend.diagnostics.run_checks',return_value=(self.root,2)) as runner:
            self.assertEqual(main(),2)
        self.assertEqual(runner.call_args.args[0],['llm'])
        self.assertFalse(runner.call_args.kwargs['live'])
