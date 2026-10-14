import json
import math
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch

from pydantic import SecretStr

import test_chunking as fixtures
from backend.ai_settings import ModelSettings
from backend.chunking import ChunkPolicy, build_chunks, load_chunk_policy, reindex_document
from backend.embedding_client import CachedEmbedder, EmbeddingClient, EmbeddingConfig, EmbeddingError, validate_vectors
from backend.pilot_db import connect, initialize
from backend.reindex_search import check_connection, reindex
from backend.semantic_chunking import sentence_units


def config(**kwargs):
    kwargs.setdefault("api_format", "openai_compatible")
    kwargs.setdefault("batch_size", 32)
    return EmbeddingConfig(ModelSettings("http://127.0.0.1:1/v1", "test-model", SecretStr("test-secret")), **kwargs)


class TopicVectors:
    """Deterministic test vectors; never presented as actual model inference."""
    def __init__(self, constant=False):
        self.calls = []
        self.constant = constant

    def embed(self, texts):
        self.calls.append(texts)
        return [[1, 0] if self.constant or "NAND" not in text else [0, 1] for text in texts]


class LocalAPI:
    def __enter__(self):
        owner = self
        self.requests = []
        self.status, self.invalid, self.redirect = 200, None, False

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append((self.path, payload, dict(self.headers)))
                status = 302 if owner.redirect else owner.status
                self.send_response(status)
                if owner.redirect:
                    self.send_header("Location", owner.url + "/must-not-follow")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if status != 200:
                    self.wfile.write(b'{"error":"secret-provider-response"}')
                    return
                texts = payload.get("input", payload.get("inputs", payload.get("sentences", [])))
                if isinstance(texts, str):
                    texts = [texts]
                vectors = TopicVectors().embed(texts)
                if "sentences" in payload:
                    response = {"result": {"vectors": vectors}}
                elif "inputs" in payload:
                    response = vectors
                else:
                    response = {"data": [{"index": i, "embedding": vector} for i, vector in reversed(list(enumerate(vectors)))]}
                if owner.invalid is not None:
                    response = owner.invalid
                self.wfile.write(json.dumps(response).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def config(self, **kwargs):
        kwargs.setdefault("api_format", "openai_compatible")
        kwargs.setdefault("batch_size", 32)
        return EmbeddingConfig(ModelSettings(self.url + "/embedding", "local-test", SecretStr("test-secret")), **kwargs)


class EmbeddingTransportTests(unittest.TestCase):
    def test_exact_singular_endpoint_batching_auth_and_out_of_order_response(self):
        with LocalAPI() as api:
            client = EmbeddingClient(api.config(batch_size=2))
            self.assertEqual(client.embed(["Retention.", "NAND erase.", "Retention again."]), [[1, 0], [0, 1], [1, 0]])
            self.assertEqual(len(api.requests), 2)
            self.assertTrue(all(r[0] == "/embedding" for r in api.requests))
            self.assertEqual(api.requests[0][2]["Authorization"], "Bearer test-secret")
            self.assertEqual(api.requests[0][1]["encoding_format"], "float")
            self.assertNotIn("test-secret", repr(client.config))
            self.assertNotIn("test-secret", json.dumps(client.config.identity()))

    def test_custom_json_contract_exact_endpoint_and_api_key_header(self):
        contract = {"request": {"sentences": "$texts"}, "response": {"items_path": ["result", "vectors"], "vector_path": [], "index_field": None}}
        with LocalAPI() as api:
            cfg = api.config(api_format="custom_json", endpoint_url=api.url + "/custom/embedding",
                             auth_header="X-API-Key", auth_scheme="", contract_json=json.dumps(contract))
            self.assertEqual(EmbeddingClient(cfg).embed(["Retention", "NAND"]), [[1, 0], [0, 1]])
            path, body, headers = api.requests[0]
            self.assertEqual(path, "/custom/embedding")
            self.assertEqual(body, {"sentences": ["Retention", "NAND"]})
            self.assertEqual(headers["X-Api-Key"], "test-secret")
            self.assertNotIn("Authorization", headers)

    def test_tei_contract_disables_server_truncation(self):
        with LocalAPI() as api:
            self.assertEqual(EmbeddingClient(api.config(api_format="tei")).embed(["NAND"]), [[0, 1]])
            self.assertFalse(api.requests[0][1]["truncate"])

    def test_confirmed_company_request_uses_single_string_and_no_extra_fields(self):
        with LocalAPI() as api:
            api.invalid = {"data": [{"embedding": [1, 0]}]}  # No unconfirmed index field required.
            cfg = EmbeddingConfig(ModelSettings(api.url + "/embedding", "local-test", SecretStr("test-secret")))
            self.assertEqual(check_connection(ChunkPolicy(mode="semantic", embedding=cfg))["vectors"], 2)
            self.assertEqual(len(api.requests), 2)
            for path, body, headers in api.requests:
                self.assertEqual(path, "/embedding")
                self.assertEqual(set(body), {"input", "model"})
                self.assertIsInstance(body["input"], str)
                self.assertEqual(body["model"], "local-test")
                self.assertEqual(headers["Authorization"], "Bearer test-secret")
                self.assertEqual(headers["Content-Type"], "application/json")

    def test_company_response_contract_drives_semantic_boundaries(self):
        with LocalAPI() as api:
            cfg = api.config(api_format="company", batch_size=1)
            policy = ChunkPolicy(mode="semantic", embedding=cfg, semantic_buffer_sentences=0)
            pages = [fixtures.page(1, "Retention is measured. NAND erase is studied.")]
            groups = build_chunks(pages, "Memory", policy, EmbeddingClient(cfg))
            self.assertEqual(len(groups), 2)
            self.assertEqual(len(api.requests), 2)
            fixtures.ChunkingTests().assert_locations(pages, groups)

    def test_single_vector_response_mappings_and_invalid_dimensions(self):
        with LocalAPI() as api:
            for payload, path in (([3, 4], []), ({"embedding": [3, 4]}, ["embedding"]),
                                  ({"result": {"vector": [3, 4]}}, ["result", "vector"])):
                contract = {"request": {"input": "$text", "model": "$model"},
                            "response": {"items_path": [], "vector_path": path, "single_vector": True}}
                cfg = api.config(api_format="custom_json", batch_size=1, contract_json=json.dumps(contract))
                api.invalid = payload
                self.assertEqual(EmbeddingClient(cfg).embed(["Retention"]), [[0.6, 0.8]])
            with self.assertRaises(EmbeddingError):
                replace(cfg, batch_size=2)
            api.invalid = {"result": {"vector": [0, 0]}}
            with self.assertRaises(EmbeddingError):
                EmbeddingClient(cfg).embed(["Retention"])

    def test_incomplete_transport_response_is_sanitized_and_retried(self):
        client = EmbeddingClient(config())
        with patch.object(client.opener, "open", side_effect=IncompleteRead(b"private-response")), patch("backend.embedding_client.time.sleep"):
            with self.assertRaises(EmbeddingError) as error:
                client.embed(["private-input"])
            self.assertEqual(client.requests, 3)
            self.assertNotIn("private", str(error.exception))

    def test_http_errors_and_redirects_are_safe_and_do_not_fallback(self):
        with LocalAPI() as api:
            for status in (400, 401, 413):
                api.status = status
                with self.assertRaises(EmbeddingError) as error:
                    EmbeddingClient(api.config()).embed(["private-input"])
                self.assertIn(str(status), str(error.exception))
                for secret in ("test-secret", "private-input", "secret-provider-response"):
                    self.assertNotIn(secret, str(error.exception))
            api.redirect = True
            with self.assertRaises(EmbeddingError):
                EmbeddingClient(api.config()).embed(["private-input"])
            self.assertFalse(any(path.endswith("must-not-follow") for path, *_ in api.requests))

    def test_retries_are_bounded_and_malformed_responses_are_rejected(self):
        with LocalAPI() as api, patch("backend.embedding_client.time.sleep"):
            api.status = 503
            with self.assertRaises(EmbeddingError):
                EmbeddingClient(api.config()).embed(["test"])
            self.assertEqual(len(api.requests), 3)
            api.status = 200
            for payload in ({"data": []}, {"data": [{"index": 2, "embedding": [1, 0]}]}, {"vectors": [[1, 0]]}):
                api.invalid = payload
                with self.assertRaises(EmbeddingError):
                    EmbeddingClient(api.config()).embed(["test"])

    def test_invalid_vectors_and_incomplete_configuration(self):
        for vectors in ([[0, 0]], [[math.nan, 1]], [[True, 1]], [[math.inf, 1]], [[1, 0], [1]], [[]]):
            with self.assertRaises(EmbeddingError):
                validate_vectors(vectors, len(vectors))
        with self.assertRaises(EmbeddingError):
            config(api_format="")
        with self.assertRaises(EmbeddingError):
            config(endpoint_url="https://name:secret@example.test/embedding")


class SemanticBoundaryTests(unittest.TestCase):
    def policy(self, **kwargs):
        return ChunkPolicy(mode="semantic", embedding=config(), semantic_buffer_sentences=0, **kwargs)

    def test_topic_boundary_depends_on_vectors_even_below_token_limit(self):
        text = " ".join([f"Retention experiment {i} measured stored charge." for i in range(4)] + [f"NAND experiment {i} measured erase behavior." for i in range(4)])
        pages = [fixtures.page(1, text)]
        policy, audit = self.policy(), []
        groups = build_chunks(pages, "Memory", policy, TopicVectors(), audit)
        self.assertEqual(len(groups), 2)
        self.assertNotIn("NAND", groups[0]["text"])
        self.assertTrue(groups[1]["text"].startswith("NAND"))
        self.assertEqual(len(build_chunks(pages, "Memory", policy, TopicVectors(constant=True))), 1)
        self.assertEqual(len(build_chunks(pages, "Memory", ChunkPolicy())), 1)
        self.assertEqual(sum(c["selected"] for c in audit[0]["candidates"]), 1)
        fixtures.ChunkingTests().assert_locations(pages, groups)

    def test_short_section_can_split_two_topics_without_identical_windows(self):
        policy = replace(self.policy(), semantic_buffer_sentences=1)
        groups = build_chunks([fixtures.page(1, "Retention is measured. NAND erase is studied.")], "Memory", policy, TopicVectors())
        self.assertEqual(len(groups), 2)

    def test_sentence_boundaries_preserve_abbreviations_decimals_and_unicode_sources(self):
        text = "Fig. 2 reports 0.1 μA. Retention exceeded 100 s. NAND erase was studied."
        units, forced = sentence_units(text, ChunkPolicy().splitter(128))
        self.assertEqual(text[units[0][0]:units[0][1]].strip(), "Fig. 2 reports 0.1 μA.")
        self.assertEqual(forced, 0)
        pages = [fixtures.page(1, "RESULTS\nFig. 2 reports 0.1 μA. Retention at 90 °C"),
                 fixtures.page(2, "exceeded 100 s. 한글 조건입니다. NAND erase was studied.")]
        groups = build_chunks(pages, "Memory", self.policy(), TopicVectors())
        fixtures.ChunkingTests().assert_locations(pages, groups)
        self.assertTrue(any(len({s["page_id"] for s in g["spans"]}) > 1 for g in groups))
        self.assertTrue(groups[0]["text"].startswith("RESULTS\nFig. 2"))

    def test_token_caps_are_only_safety_splits_for_a_single_topic(self):
        text = "RESULTS\n" + "Retention at 90 °C was >100 s. " * 100
        policy = self.policy(max_tokens=160, overlap_tokens=16, parent_max_tokens=400)
        audit = []
        pages = [fixtures.page(1, text)]
        groups = build_chunks(pages, "Memory", policy, TopicVectors(constant=True), audit)
        self.assertEqual(audit[0]["semantic_groups"], 1)
        self.assertGreater(audit[0]["token_limit_parent_splits"], 0)
        for group in groups:
            self.assertEqual(len(policy.splitter(400).chunks(group["text"])), 1)
            for child in group["children"]:
                self.assertEqual(len(policy.splitter(160).chunks(child["context_text"])), 1)
        fixtures.ChunkingTests().assert_locations(pages, groups)

    def test_input_budget_and_long_sentence_splitting_do_not_truncate_sources(self):
        policy = replace(self.policy(), embedding=config(max_input_tokens=128))
        fake = TopicVectors()
        pages = [fixtures.page(1, "Retention data " * 200 + ". NAND erase was studied.")]
        audit = []
        groups = build_chunks(pages, "Memory", policy, fake, audit)
        self.assertGreater(audit[0]["forced_unit_splits"], 0)
        self.assertTrue(all(len(policy.splitter(128).chunks(t)) == 1 for batch in fake.calls for t in batch))
        fixtures.ChunkingTests().assert_locations(pages, groups)
        policy = replace(policy, embedding=config(max_input_tokens=128, input_prefix="oversized prefix " * 200))
        with self.assertRaises(EmbeddingError):
            build_chunks(pages, "Memory", policy, TopicVectors())

    def test_sections_and_unreadable_pages_are_never_joined(self):
        pages = [fixtures.page(1, "INTRODUCTION\nRetention is measured. NAND is compared."),
                 fixtures.page(2, "private unreadable page", "needs_ocr"),
                 fixtures.page(3, "RESULTS\nRetention improved. NAND erase improved.")]
        groups = build_chunks(pages, "Memory", self.policy(), TopicVectors())
        self.assertTrue(all(len({s["page_number"] for s in g["spans"]}) == 1 for g in groups))
        fixtures.ChunkingTests().assert_locations(pages, groups)


class SemanticIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.fixtures.PilotTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_cache_reuse_and_model_revision_invalidation(self):
        with connect(self.fixture.db) as db:
            client = TopicVectors()
            cached = CachedEmbedder(db, config(), client)
            self.assertEqual(cached.embed(["Retention", "NAND", "Retention"]), [[1, 0], [0, 1], [1, 0]])
            self.assertEqual(len(client.calls[0]), 2)
            CachedEmbedder(db, config(), client).embed(["Retention", "NAND"])
            self.assertEqual(len(client.calls), 1)
            CachedEmbedder(db, config(revision="v2"), client).embed(["Retention", "NAND"])
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(db.execute("SELECT count(*) FROM chunk_embedding_cache").fetchone()[0], 4)

    def test_index_provenance_atomicity_cache_and_audit_over_real_http(self):
        with LocalAPI() as api, connect(self.fixture.db) as db:
            run = db.execute("SELECT current_run_id FROM paper_documents").fetchone()[0]
            sources = [tuple(row) for row in db.execute("SELECT * FROM pages")]
            policy = ChunkPolicy(mode="semantic", embedding=api.config())
            self.assertEqual(reindex_document(db, run, policy)["status"], "indexed")
            self.assertTrue(db.execute("SELECT details_json FROM search_semantic_audits").fetchone())
            self.assertTrue(db.execute("SELECT 1 FROM chunk_embedding_cache").fetchone())
            calls = len(api.requests)
            self.assertEqual(reindex_document(db, run, policy)["status"], "unchanged")
            self.assertEqual(len(api.requests), calls)
            reindex_document(db, run, replace(policy, semantic_percentile=80))
            self.assertEqual(len(api.requests), calls)  # Reuse vectors when only boundary threshold changes.
            before = {name: [tuple(r) for r in db.execute('SELECT * FROM "' + name + '"')] for name in
                      ("search_chunks", "chunk_fts", "search_contexts", "search_index_state", "search_semantic_audits", "chunk_embedding_cache")}
            api.status = 401
            with self.assertRaises(EmbeddingError):
                reindex_document(db, run, replace(policy, embedding=replace(policy.embedding, revision="v2")))
            for name, rows in before.items():
                self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM "' + name + '"')], rows)
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM pages")], sources)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_configuration_auto_resolution_company_defaults_and_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            env.write_text("CHUNKING_MODE=auto\n", encoding="utf-8")
            self.assertEqual(load_chunk_policy(env, environ={}).mode, "structural")
            with self.assertRaises(EmbeddingError):
                load_chunk_policy(env, environ={"EMBEDDING_BASE_URL": "http://127.0.0.1/embedding"})
            with self.assertRaises(EmbeddingError):
                load_chunk_policy(env, environ={"CHUNKING_MODE": "semantic"})
            with LocalAPI() as api:
                policy = load_chunk_policy(env, environ={"EMBEDDING_ENDPOINT": api.url + "/embedding", "EMBEDDING_MODEL": "test", "EMBEDDING_API_FORMAT": "openai_compatible"})
                self.assertEqual(policy.mode, "semantic")
                self.assertEqual(check_connection(policy)["dimensions"], 2)
                self.assertEqual(len(api.requests), 1)
                self.assertEqual(api.requests[0][0], "/embedding")
                self.assertIn("A DRAM cell", api.requests[0][1]["input"][0])
                company = load_chunk_policy(env, environ={"EMBEDDING_BASE_URL": api.url + "/embedding", "EMBEDDING_MODEL": "test"})
                self.assertEqual(company.embedding.api_format, "company")
                self.assertEqual(company.embedding.batch_size, 1)
                self.assertEqual(check_connection(company)["dimensions"], 2)
                self.assertIsInstance(api.requests[-1][1]["input"], str)
            self.assertEqual(load_chunk_policy(env, environ={"CHUNKING_MODE": "structural", "EMBEDDING_MODEL": "test"}).mode, "structural")

    def test_whole_corpus_reindex_failure_rolls_back_prior_documents(self):
        from backend.pilot_ingest import ingest_file
        pdf = self.fixture.root / "second.pdf"
        fixtures.fixtures.make_pdf(pdf, fixtures.fixtures.TEXT.replace("DRAM", "NAND"))
        ingest_file(pdf, self.fixture.db, 0)
        with connect(self.fixture.db) as db:
            before = [tuple(r) for r in db.execute("SELECT * FROM search_chunks")]
        policy = ChunkPolicy(mode="semantic", embedding=config())
        calls = [0]

        def fail_second(texts):
            calls[0] += 1
            if calls[0] == 2:
                raise EmbeddingError("Controlled failure")
            return TopicVectors().embed(texts)

        with patch("backend.reindex_search.load_chunk_policy", return_value=policy), patch("backend.embedding_client.EmbeddingClient.embed", side_effect=fail_second):
            with self.assertRaises(EmbeddingError):
                reindex(self.fixture.db)
        with connect(self.fixture.db) as db:
            self.assertEqual([tuple(r) for r in db.execute("SELECT * FROM search_chunks")], before)
            self.assertEqual(db.execute("SELECT count(*) FROM chunk_embedding_cache").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
