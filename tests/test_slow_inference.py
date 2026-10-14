"""Slow internal inference: time budgets, shared capacity, and safe partial progress."""
import io
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from pydantic import SecretStr
from backend.ai_settings import InferenceLimits, ModelSettings, load_ai_settings
from backend.llm_client import CompanyLLM, LLMError, LLMTimeoutError, LLMCapacityError, ModelCallGate
from backend.research_progress import ResearchControl, ResearchCancelled, _current
from backend.pilot_db import connect
from backend.wiki_engine import scheduled_lint_ready
from backend.wiki_lint import run_lint
from backend.wiki_api import Question, answer
from backend.answer_trace import read_run
from backend.wiki_store import digest
import test_research_answers as research_fixtures
import test_markdown_wiki as wiki_fixtures


SERVICE = ModelSettings("http://127.0.0.1:1/v1", "test-model", SecretStr("synthetic-secret"))


class SlowSettingsTests(unittest.TestCase):
    def test_defaults_file_override_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/".env"
            defaults = load_ai_settings(path, environ={}).limits
            self.assertEqual(defaults.llm_timeout_seconds, 300)
            self.assertEqual(defaults.research_timeout_seconds, 1500)
            path.write_text("LLM_TIMEOUT_SECONDS=600\nLLM_QUEUE_TIMEOUT_SECONDS=180\n"
                            "LLM_MAX_CONCURRENT_REQUESTS=1\nRESEARCH_TIMEOUT_SECONDS=2400\n"
                            "RERANKER_TIMEOUT_SECONDS=90\n", encoding="utf-8")
            values = load_ai_settings(path, environ={"LLM_TIMEOUT_SECONDS":"450"}).limits
            self.assertEqual(values, InferenceLimits(450,180,1,2400,90))
            with patch("backend.llm_client.load_ai_settings", return_value=load_ai_settings(path, environ={})):
                self.assertEqual(CompanyLLM(SERVICE).timeout_seconds, 600)

    def test_invalid_limits_fail_without_echoing_values(self):
        for name, value in (("LLM_TIMEOUT_SECONDS","nan"),("LLM_TIMEOUT_SECONDS","0"),
                            ("LLM_QUEUE_TIMEOUT_SECONDS","inf"),("RESEARCH_TIMEOUT_SECONDS","7201"),
                            ("LLM_MAX_CONCURRENT_REQUESTS","2.5"),("LLM_MAX_CONCURRENT_REQUESTS","9"),
                            ("RERANKER_TIMEOUT_SECONDS","synthetic-secret")):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError) as caught:
                load_ai_settings(Path("absent-test-env"), environ={name:value})
            self.assertIn(name, str(caught.exception))
            self.assertNotIn("synthetic-secret", str(caught.exception))

    def test_embedding_and_reranker_use_independent_timeouts(self):
        from backend.chunking import load_chunk_policy
        from backend.wiki_retrieval import rerank
        policy = load_chunk_policy(Path("absent-test-env"), environ={
            "EMBEDDING_BASE_URL":"http://127.0.0.1:1/embedding", "EMBEDDING_MODEL":"test",
            "EMBEDDING_TIMEOUT_SECONDS":"300", "LLM_TIMEOUT_SECONDS":"600"})
        self.assertEqual(policy.embedding.timeout_seconds,300)
        response = io.BytesIO(b'{"results":[{"index":0,"relevance_score":1}]}')
        settings = load_ai_settings(Path("absent-test-env"), environ={"RERANKER_TIMEOUT_SECONDS":"90"})
        with patch("urllib.request.build_opener") as opener, \
             patch("backend.wiki_retrieval.load_ai_settings", return_value=settings):
            opener.return_value.open.return_value = response
            self.assertEqual(rerank("query",[{"text":"one"}],SERVICE),[{"text":"one"}])
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"],90)

    def test_embedding_timeout_does_not_duplicate_a_still_running_request(self):
        from backend.embedding_client import EmbeddingClient, EmbeddingError
        from test_semantic_chunking import config
        for error in (TimeoutError(), URLError(TimeoutError()),
                      HTTPError(SERVICE.base_url,504,"gateway",{},None)):
            client=EmbeddingClient(config(timeout_seconds=300))
            with patch.object(client.opener,"open",side_effect=error):
                with self.assertRaises(EmbeddingError):
                    client.embed(["Synthetic test sentence."])
            self.assertEqual(client.requests,1)


class SlowLLMClientTests(unittest.TestCase):
    def setUp(self):
        self.gate = ModelCallGate()
        self.replace_gate = patch("backend.llm_client._model_gate", self.gate)
        self.replace_gate.start()
        self.addCleanup(self.replace_gate.stop)

    def test_default_timeout_is_sent_without_changing_company_contract(self):
        content = json.dumps({"choices":[{"finish_reason":"stop","message":{"content":"{\"sections\":[]}"}}]}).encode()
        with patch("backend.llm_client.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(content)
            client = CompanyLLM(SERVICE,limits=InferenceLimits())
            self.assertEqual(client.complete_json("system",{"evidence":[]}),{"sections":[]})
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"],300)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(set(json.loads(request.data)),{"model","messages"})
            self.assertEqual(client.call_history[0]["status"],"completed")
            self.assertNotIn("synthetic-secret",str(client.call_history))

    def test_timeout_capacity_and_invalid_json_are_distinct_without_auto_retry(self):
        for failure, expected in [
            (TimeoutError(),LLMTimeoutError),
            (URLError(TimeoutError()),LLMTimeoutError),
            (HTTPError(SERVICE.base_url,504,"gateway",{},None),LLMTimeoutError),
            (HTTPError(SERVICE.base_url,429,"busy",{},None),LLMCapacityError),
            (HTTPError(SERVICE.base_url,503,"busy",{},None),LLMCapacityError)]:
            with self.subTest(expected=expected), patch("backend.llm_client.build_opener") as opener:
                opener.return_value.open.side_effect = failure
                with self.assertRaises(expected) as caught:
                    CompanyLLM(SERVICE).complete_json("system",{})
                self.assertEqual(opener.return_value.open.call_count,1)
                self.assertNotIn("synthetic-secret",str(caught.exception))
                self.assertEqual(self.gate.active,0)
        with patch("backend.llm_client.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(b'not json')
            with self.assertRaises(LLMError) as caught:
                CompanyLLM(SERVICE).complete_json("system",{})
            self.assertNotIsInstance(caught.exception,LLMTimeoutError)

    def test_real_delayed_headers_timeout_once_then_release_capacity(self):
        calls, release, entered = [], threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                calls.append(1);entered.set()
                release.wait(2)
                try:
                    self.send_response(200);self.end_headers()
                    self.wfile.write(b'{"choices":[{"message":{"content":"{}"}}]}')
                except OSError:pass
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=CompanyLLM(replace(SERVICE,base_url=f"http://127.0.0.1:{server.server_port}/v1"))
            client.timeout_seconds=0.05
            with self.assertRaises(LLMTimeoutError):
                client.complete_json("system",{})
            self.assertTrue(entered.is_set())
            self.assertEqual(len(calls),1)
            self.assertEqual(self.gate.active,0)
        finally:
            release.set();server.shutdown();server.server_close();thread.join()

    def test_two_clients_share_one_capacity_limit_and_queue_without_resending(self):
        entered, release = threading.Event(), threading.Event()
        limits=InferenceLimits(llm_max_concurrent_requests=1)
        first,second=CompanyLLM(SERVICE,limits=limits),CompanyLLM(SERVICE,limits=limits)
        calls=[]
        def transport(system,payload,timeout):
            calls.append(payload["id"])
            if payload["id"]==1:
                entered.set()
                self.assertTrue(release.wait(3))
            return {}
        with patch.object(CompanyLLM,"_request_json",side_effect=transport), ThreadPoolExecutor(2) as pool:
            a=pool.submit(first.complete_json,"system",{"id":1})
            self.assertTrue(entered.wait(2))
            b=pool.submit(second.complete_json,"system",{"id":2})
            try:
                deadline=time.monotonic()+2
                while not self.gate.waiters and time.monotonic()<deadline:time.sleep(0.01)
                self.assertEqual(calls,[1])
                self.assertEqual(self.gate.active,1)
                self.assertTrue(self.gate.waiters)
            finally:release.set()
            self.assertEqual(a.result(3),{});self.assertEqual(b.result(3),{})
        self.assertEqual(calls,[1,2])
        self.assertEqual(self.gate.active,0)

    def test_queued_cancellation_and_queue_timeout_never_make_http_request(self):
        control=ResearchControl(lambda event:None)
        queued=CompanyLLM(SERVICE,limits=InferenceLimits(llm_max_concurrent_requests=1))
        def request():
            token=_current.set(control)
            try:return queued.complete_json("system",{})
            finally:_current.reset(token)
        with self.gate.slot(1,time.monotonic()+5), patch("backend.llm_client.build_opener") as opener, ThreadPoolExecutor(1) as pool:
            future=pool.submit(request)
            try:
                deadline=time.monotonic()+2
                while not self.gate.waiters and time.monotonic()<deadline:time.sleep(0.01)
                control.cancelled.set()
                with self.assertRaises(ResearchCancelled):future.result(2)
                with self.assertRaises(LLMCapacityError):
                    queued.complete_json("system",{},deadline=time.monotonic()+0.03)
                opener.assert_not_called()
                self.assertFalse(self.gate.waiters)
                self.assertEqual(self.gate.active,1)
            finally:control.cancelled.set()

    def test_cancelled_inflight_request_keeps_capacity_until_it_returns(self):
        entered,release=threading.Event(),threading.Event()
        control=ResearchControl(lambda event:None)
        client=CompanyLLM(SERVICE,limits=InferenceLimits(llm_max_concurrent_requests=1))
        def transport(*args):
            entered.set();release.wait(3)
            return {}
        def work():
            token=_current.set(control)
            try:return client.complete_json("system",{})
            finally:_current.reset(token)
        with patch.object(client,"_request_json",side_effect=transport),ThreadPoolExecutor(1) as pool:
            task=pool.submit(work)
            try:
                self.assertTrue(entered.wait(2))
                control.cancelled.set()
                self.assertEqual(self.gate.active,1)
            finally:release.set()
            with self.assertRaises(ResearchCancelled):task.result(2)
        self.assertEqual(self.gate.active,0)

    def test_late_result_is_rejected_and_remaining_budget_caps_http_wait(self):
        clock=[100.]
        seen=[]
        client=CompanyLLM(SERVICE)
        def transport(system,payload,timeout):
            seen.append(timeout);clock[0]=151.
            return {}
        with patch("backend.llm_client.time.monotonic",side_effect=lambda:clock[0]), \
             patch.object(client,"_request_json",side_effect=transport):
            with self.assertRaises(LLMTimeoutError):
                client.complete_json("system",{},deadline=150.)
        self.assertEqual(seen,[50.])
        self.assertEqual(self.gate.active,0)


class SlowResearchTests(unittest.TestCase):
    setUp=research_fixtures.ResearchAnswerTests.setUp
    tearDown=research_fixtures.ResearchAnswerTests.tearDown
    curation=research_fixtures.ResearchAnswerTests.curation
    sync=research_fixtures.ResearchAnswerTests.sync

    def test_four_slow_phases_complete_after_old_240_second_limit(self):
        clock=[1000.]
        model=research_fixtures.ResearchLLM()
        client=CompanyLLM(SERVICE,limits=InferenceLimits())
        waits=[]
        def transport(system,payload,timeout):
            waits.append(timeout);clock[0]+=120
            return model.complete_json(system,payload)
        with patch("backend.llm_client.time.monotonic",side_effect=lambda:clock[0]), \
             patch.object(client,"_request_json",side_effect=transport):
            result=answer(Question(question="refresh 74",expert_ids=["dram"]),client)
        self.assertEqual(model.operations,["plan","refine","answer","review"])
        self.assertEqual(waits,[300]*4)
        self.assertEqual(result["synthesis"]["mode"],"llm")
        run=read_run(result["run_id"])
        self.assertEqual(run["trace"]["limits"]["scheduling_seconds"],1500)
        self.assertEqual(len(client.call_history),4)

    def test_overload_or_timeout_stops_later_phases(self):
        for error in (LLMTimeoutError("Controlled timeout"),LLMCapacityError("Controlled overload")):
            model=research_fixtures.ResearchLLM(plan=error)
            result=answer(Question(question="refresh 74",expert_ids=["dram"]),model)
            self.assertEqual(model.operations,["plan"])
            self.assertEqual(result["synthesis"]["mode"],"evidence")
            self.assertIn(str(error),result["synthesis"]["notice"])


class SlowWikiTests(unittest.TestCase):
    setUp=wiki_fixtures.MarkdownWikiTests.setUp
    tearDown=wiki_fixtures.MarkdownWikiTests.tearDown
    curation=wiki_fixtures.MarkdownWikiTests.curation
    sync=wiki_fixtures.MarkdownWikiTests.sync

    def test_scheduled_lint_yields_to_wiki_jobs_and_active_model_calls(self):
        self.assertFalse(scheduled_lint_ready(self.db))
        self.sync()
        with connect(self.db) as db:
            db.execute("UPDATE wiki_jobs SET state='done'");db.commit()
        with patch("backend.llm_client.model_requests_active",return_value=True):
            self.assertFalse(scheduled_lint_ready(self.db))
        with patch("backend.llm_client.model_requests_active",return_value=False):
            self.assertTrue(scheduled_lint_ready(self.db))

    def test_wiki_overload_keeps_other_scopes_queued_for_cooldown(self):
        from backend.wiki_engine import enqueue, run_jobs
        with connect(self.db) as db:
            db.execute("UPDATE wiki_jobs SET state='done'")
            first=enqueue(db,"slow common",scope="common")
            second=enqueue(db,"waiting expert",scope="expert",expert_id="dram")
            db.commit()
        with patch("backend.wiki_engine.reconcile",side_effect=LLMTimeoutError("Controlled timeout")) as model:
            result=run_jobs(self.db,wiki_fixtures.FakeLLM())
        self.assertEqual(model.call_count,1)
        self.assertEqual(result["retry_after_seconds"],60)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT state FROM wiki_jobs WHERE id=?",(first,)).fetchone()[0],"failed")
            self.assertEqual(db.execute("SELECT state FROM wiki_jobs WHERE id=?",(second,)).fetchone()[0],"queued")

    def test_lint_resumes_after_last_successful_piece_when_next_piece_times_out(self):
        self.sync()
        row=next(r for r in self.store.rows() if r["kind"]=="topic")
        refs=json.loads(row["refs_json"])
        body="# Long checked topic\n\n" + "This is a source-grounded test paragraph. "*800
        self.assertGreater(len(body),28000)
        row=self.store.publish(row["id"],row["path"],row["title"],body,refs,digest("lint-timeout-case"),
                               scope=row["scope"],expert_id=row["expert_id"],kind=row["kind"])
        llm=wiki_fixtures.FakeLLM()
        pieces=[]
        def check(system,payload):
            if "source-grounded test paragraph." in payload["wiki"]:
                pieces.append(payload["wiki"])
                if len(pieces)==2:raise LLMTimeoutError("Controlled timeout")
            return {"findings":[]}
        with patch.object(llm,"complete_json",side_effect=check):
            result=run_lint(self.db,llm=llm)
        with connect(self.db) as db:
            cached=db.execute("SELECT next_offset FROM wiki_lint_checks WHERE page_id=?",(row["id"],)).fetchone()
        self.assertIsNotNone(cached)
        self.assertEqual(cached[0],14000)
        # Permit the explicit follow-up lint; cache identity stays the same.
        def resume(system,payload):
            if "source-grounded test paragraph." in payload["wiki"]:
                pieces.append(payload["wiki"])
            return {"findings":[]}
        with patch.object(llm,"complete_json",side_effect=resume):
            run_lint(self.db,llm=llm)
        self.assertEqual(pieces[2],body[14000:28000])


if __name__=="__main__":
    unittest.main()

