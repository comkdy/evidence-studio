"""Regression tests using independent SQLite connections during model work."""
import json
import sqlite3
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, closing
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from backend.main import app
from backend.chunking import (ChunkPolicy, IndexInputsChanged, prepare_document_index,
                              reindex_document)
from backend.pilot_db import connect, write_connection, is_database_busy
from backend.pilot_ingest import ingest_file
from backend.pilot_curate import apply_curation
from backend.processing import process_next, TaskUpdateDeferred, update_task
from backend.reindex_search import reindex
from backend.wiki_engine import run_jobs, enqueue
import test_processing as processing_fixtures
import test_pilot as pilot_fixtures
from test_pilot import TEXT
from test_semantic_chunking import config, TopicVectors
from test_markdown_wiki import FakeLLM


class DatabaseConcurrencyTests(unittest.TestCase):
    setUp = processing_fixtures.ProcessingTests.setUp
    tearDown = processing_fixtures.ProcessingTests.tearDown
    pdf = processing_fixtures.ProcessingTests.pdf
    task = processing_fixtures.ProcessingTests.task

    def policy(self):
        return ChunkPolicy(mode="semantic", embedding=config())

    def writer(self):
        with connect(self.db, timeout=0.15) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE expert_categories SET name=name WHERE id='dram'")
            db.commit()

    def model(self, texts):
        # Must finish while the simulated model request is still in flight.
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(self.writer).result(timeout=3)
        return TopicVectors().embed(texts)

    def ingest(self):
        path = self.pdf()
        self.assertEqual(ingest_file(path, self.db, 0)["status"], "ready")
        return path

    def run_id(self):
        with connect(self.db) as db:
            return db.execute("SELECT current_run_id FROM paper_documents").fetchone()[0]

    def snapshot(self, tables):
        with connect(self.db) as db:
            return {table: [tuple(r) for r in db.execute("SELECT * FROM " + table)] for table in tables}

    def test_ingest_has_no_partial_paper_or_write_lock_during_embedding(self):
        def embed(texts):
            self.assertEqual(self.snapshot(["papers", "chunk_embedding_cache"]),
                             {"papers": [], "chunk_embedding_cache": []})
            return self.model(texts)
        with patch("backend.pilot_ingest.load_chunk_policy", return_value=self.policy()), \
             patch("backend.embedding_client.EmbeddingClient.embed", side_effect=embed) as api:
            self.ingest()
        self.assertGreaterEqual(api.call_count, 2)  # Covers cache writes between sections.
        with connect(self.db) as db:
            self.assertTrue(db.execute("SELECT 1 FROM chunk_embedding_cache").fetchone())
            self.assertFalse(db.execute("PRAGMA foreign_key_check").fetchall())

    def test_wiki_worker_can_finish_while_ingest_embedding_waits(self):
        with connect(self.db) as db:
            enqueue(db, "concurrent empty-corpus Wiki")
            db.commit()
        entered, release = threading.Event(), threading.Event()
        def embed(texts):
            entered.set()
            if not release.wait(10):
                raise AssertionError("Wiki worker could not finish during model call")
            return TopicVectors().embed(texts)
        path = self.pdf()
        with patch("backend.pilot_ingest.load_chunk_policy", return_value=self.policy()), \
             patch("backend.embedding_client.EmbeddingClient.embed", side_effect=embed), \
             ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(ingest_file, path, self.db, 0)
            try:
                self.assertTrue(entered.wait(5))
                result = executor.submit(run_jobs, self.db, FakeLLM()).result(timeout=5)
                self.assertNotIn(result.get("status"), ("failed", "busy", "deferred"))
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5)["status"], "ready")

    def test_semantic_reindex_and_corpus_reindex_allow_concurrent_writes(self):
        self.ingest()
        with patch("backend.embedding_client.EmbeddingClient.embed", side_effect=self.model):
            with connect(self.db) as db:
                self.assertEqual(reindex_document(db, self.run_id(), self.policy())["status"], "indexed")
        # Use another model identity so this path cannot pass only through cache hits.
        policy = ChunkPolicy(mode="semantic", embedding=config(revision="corpus"))
        with patch("backend.reindex_search.load_chunk_policy", return_value=policy), \
             patch("backend.embedding_client.EmbeddingClient.embed", side_effect=self.model) as api:
            self.assertEqual(reindex(self.db)["rebuilt"], 1)
            self.assertGreater(api.call_count, 0)

    def test_stale_prepared_index_is_rejected_without_losing_previous_index(self):
        self.ingest()
        before = self.snapshot(["search_chunks", "search_index_state", "chunk_embedding_cache"])
        with connect(self.db) as db, patch("backend.embedding_client.EmbeddingClient.embed", side_effect=self.model):
            prepared = prepare_document_index(db, self.run_id(), self.policy())
            with connect(self.db) as other:
                other.execute("UPDATE pages SET text=text || ' Concurrent correction.'")
                other.commit()
            with self.assertRaises(IndexInputsChanged):
                reindex_document(db, self.run_id(), self.policy(), prepared=prepared)
            self.assertFalse(db.in_transaction)
        self.assertEqual(self.snapshot(before), before)

    def test_commit_failure_rolls_back_index_and_preserves_original_error(self):
        self.ingest()
        before = self.snapshot(["search_chunks", "search_index_state", "chunk_embedding_cache"])
        with connect(self.db) as db:
            class CommitFailure:
                def __getattr__(self, name):
                    return getattr(db, name)
                def commit(self):
                    raise sqlite3.OperationalError("database is locked")
            with patch("backend.embedding_client.EmbeddingClient.embed", side_effect=self.model):
                with self.assertRaisesRegex(sqlite3.OperationalError, "database is locked"):
                    reindex_document(CommitFailure(), self.run_id(), self.policy())
            self.assertFalse(db.in_transaction)
        self.assertEqual(self.snapshot(before), before)

    def test_semantic_work_inside_write_transaction_is_rejected_before_api(self):
        self.ingest()
        with connect(self.db) as db, patch("backend.embedding_client.EmbeddingClient.embed") as api:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(ValueError, "before opening"):
                reindex_document(db, self.run_id(), self.policy())
            api.assert_not_called()
            db.rollback()

    def test_title_curation_prepares_embeddings_before_atomic_changes(self):
        path = self.ingest()
        fixture = pilot_fixtures.PilotTests()
        fixture.db, fixture.root, fixture.pdf = self.db, self.root, path
        target, payload = fixture.curation()
        payload["papers"][0]["corrected_title"] = "Reviewed DRAM Retention Study"
        payload["papers"][0]["title_source_quote"] = {"page": 1, "quote": "DRAM Retention Study"}
        target.write_text(json.dumps(payload), encoding="utf-8")
        with patch("backend.pilot_curate.load_chunk_policy", return_value=self.policy()), \
             patch("backend.embedding_client.EmbeddingClient.embed", side_effect=self.model) as api:
            self.assertEqual(apply_curation(target, self.db)["status"], "created")
            self.assertGreater(api.call_count, 0)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT title FROM papers").fetchone()[0], "Reviewed DRAM Retention Study")
            self.assertEqual(db.execute("SELECT count(*) FROM claims").fetchone()[0], 1)

    def test_page_correction_and_approval_prepare_without_write_lock(self):
        self.ingest()
        with connect(self.db) as db:
            page_id = db.execute("SELECT id FROM pages").fetchone()[0]
        url = "/api/pilot/page-reviews/" + page_id
        with closing(TestClient(app, headers={"Origin": "http://testserver"})) as client:
            draft = dict(request_id=str(uuid4()), revision=client.get(url).json()["revision"],
                         action="correct", text=TEXT + " Additional source caption.",
                         reason="Checked against source PDF", confirmed=True)
            with patch("backend.page_review.has_permission", return_value=False):
                response = client.post(url, json=draft)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["state"], "pending")
            with patch("backend.page_review.load_chunk_policy", return_value=self.policy()), \
                 patch("backend.embedding_client.EmbeddingClient.embed", side_effect=self.model) as api:
                response = client.post("/api/pilot/page-review-requests/" + draft["request_id"] + "/decision",
                                       json=dict(action="apply", revision=draft["revision"], confirmed=True))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertGreater(api.call_count, 0)
                draft.update(request_id=str(uuid4()), revision=client.get(url).json()["revision"],
                             text=TEXT + " A second unique caption describes the tested devices.")
                response = client.post(url, json=draft)
                self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.snapshot(["pages"])["pages"][0][3], draft["text"])

    def test_concurrent_page_edit_is_not_overwritten_by_prepared_review(self):
        self.ingest()
        with connect(self.db) as db:
            page_id = db.execute("SELECT id FROM pages").fetchone()[0]
        changed = [False]
        def embed(texts):
            if not changed[0]:
                with connect(self.db) as other:
                    other.execute("UPDATE papers SET title='Concurrent title correction'")
                    other.commit()
                changed[0] = True
            return self.model(texts)
        with closing(TestClient(app, headers={"Origin": "http://testserver"})) as client:
            url = "/api/pilot/page-reviews/" + page_id
            original = client.get(url).json()
            draft = dict(request_id=str(uuid4()), revision=original["revision"], action="correct",
                         text=TEXT + " Extra caption.", reason="Checked source", confirmed=True)
            with patch("backend.page_review.load_chunk_policy", return_value=self.policy()), \
                 patch("backend.embedding_client.EmbeddingClient.embed", side_effect=embed):
                response = client.post(url, json=draft)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(client.get(url).json()["page"]["text"], original["page"]["text"])
        self.assertFalse(self.snapshot(["page_review_changes"])["page_review_changes"])

    def test_transient_wiki_lock_requeues_then_resumes(self):
        self.ingest()
        with patch("backend.wiki_engine.reconcile", side_effect=sqlite3.OperationalError("database is locked")):
            self.assertEqual(run_jobs(self.db, FakeLLM())["status"], "deferred")
        with connect(self.db) as db:
            self.assertTrue(all(r[0] == "queued" for r in db.execute("SELECT state FROM wiki_jobs")))
            self.assertFalse(db.execute("SELECT 1 FROM wiki_worker_lease").fetchone())
        self.assertNotEqual(run_jobs(self.db, FakeLLM()).get("status"), "failed")
        with connect(self.db) as db:
            self.assertTrue(all(r[0] == "done" for r in db.execute("SELECT state FROM wiki_jobs")))

    def test_wiki_llm_has_no_writer_lock_and_lease_renews_during_call(self):
        self.ingest()
        llm = FakeLLM()
        complete = llm.complete_json
        def slow(system, payload):
            self.writer()
            with connect(self.db) as db:
                before = db.execute("SELECT expires_at FROM wiki_worker_lease").fetchone()[0]
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with connect(self.db) as db:
                    after = db.execute("SELECT expires_at FROM wiki_worker_lease").fetchone()[0]
                if after > before:
                    break
                time.sleep(0.01)
            self.assertGreater(after, before)
            self.assertEqual(run_jobs(self.db, FakeLLM())["status"], "busy")
            return complete(system, payload)
        with patch("backend.wiki_engine.LEASE_RENEW_SECONDS", 0.02), \
             patch.object(llm, "complete_json", side_effect=slow):
            self.assertNotEqual(run_jobs(self.db, llm).get("status"), "failed")
        self.assertTrue(llm.calls)

    def test_ingest_lock_is_retried_not_reported_as_invalid_pdf(self):
        self.pdf()
        self.scanner.scan()
        with patch("backend.pilot_ingest.ingest_file", side_effect=sqlite3.OperationalError("database is locked")):
            self.assertFalse(process_next(self.db))
        self.assertEqual(self.task()["state"], "queued")
        self.assertIn("DB", self.task()["stage"])
        self.assertTrue(process_next(self.db))
        self.assertEqual(self.task()["state"], "db_ready")

    def test_failed_ledger_update_retains_finished_ingest_result(self):
        self.pdf()
        self.scanner.scan()
        original = update_task
        def ledger(database, task_id, **values):
            if values.get("state") == "db_ready":
                raise sqlite3.OperationalError("database is locked")
            return original(database, task_id, **values)
        with patch("backend.processing.update_task", side_effect=ledger):
            with self.assertRaises(TaskUpdateDeferred) as raised:
                process_next(self.db)
        pending = raised.exception
        self.assertEqual(pending.values["state"], "db_ready")
        update_task(self.db, pending.task_id, **pending.values)
        self.assertFalse(process_next(self.db))
        self.assertEqual(len(self.snapshot(["papers"])["papers"]), 1)

    def test_write_acquisition_retries_but_never_replays_body(self):
        calls = []
        @contextmanager
        def transient(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
            with connect(*args, **kwargs) as db:
                yield db
        with patch("backend.pilot_db.connect", side_effect=transient):
            with write_connection(self.db) as db:
                db.execute("UPDATE expert_categories SET name=name")
        self.assertEqual(len(calls), 2)
        with patch("backend.pilot_db.connect", wraps=connect) as opened:
            with self.assertRaises(sqlite3.OperationalError):
                with write_connection(self.db):
                    raise sqlite3.OperationalError("database is locked")
            self.assertEqual(opened.call_count, 1)
        self.assertFalse(is_database_busy(sqlite3.OperationalError("no such table: missing")))


if __name__ == "__main__":
    unittest.main()

