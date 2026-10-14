import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.chunking import ChunkPolicy, build_chunks, load_chunk_policy, reindex_document
from backend.pilot_api import search
from backend.pilot_db import connect
import test_pilot as fixtures


def page(number, text, status="ready"):
    return {"id": "page-" + str(number), "page_number": number, "text": text, "status": status}


class ChunkingTests(unittest.TestCase):
    def assert_locations(self, pages, groups):
        originals = {p["id"]: p["text"] for p in pages}
        for group in groups:
            for item in [group, *group["children"]]:
                covered = set()
                for span in item["spans"]:
                    self.assertEqual(item["text"][span["chunk_start"]:span["chunk_end"]],
                                     originals[span["page_id"]][span["start_offset"]:span["end_offset"]])
                    covered.update(range(span["chunk_start"], span["chunk_end"]))
                self.assertTrue(all(i in covered or char.isspace() for i, char in enumerate(item["text"])))

    def test_cross_page_sentence_unicode_and_numeric_provenance(self):
        pages = [page(1, "INTRODUCTION\nRetention at 90 °C was > 100 s, with a current of"),
                 page(2, "0.1 μA. 한글 조건을 보존합니다. The measured value was not a model estimate.")]
        groups = build_chunks(pages, "Memory", ChunkPolicy())
        children = [c for g in groups for c in g["children"]]
        self.assertTrue(any(len({s["page_number"] for s in c["spans"]}) == 2 for c in children))
        self.assertIn("> 100 s", children[0]["text"])
        self.assertIn("0.1 μA", children[0]["text"])
        self.assert_locations(pages, groups)

    def test_sections_quality_gaps_and_reference_content_are_preserved(self):
        pages = [page(1, "INTRODUCTION\nMemory retention was measured.\nMETHODS\nDevices were tested."),
                 page(2, "unreadable OCR text", "needs_ocr"),
                 page(3, "RESULTS\nRetention improved.\nREFERENCES\n[1] A separate source.")]
        groups = build_chunks(pages, "Memory", ChunkPolicy())
        text = " ".join(g["text"] for g in groups)
        self.assertIn("separate source", text)
        self.assertNotIn("unreadable", text)
        self.assertEqual([g["section"] for g in groups], ["INTRODUCTION", "METHODS", "RESULTS", "REFERENCES"])
        self.assertTrue(all(len({s["page_number"] for s in g["spans"]}) == 1 for g in groups))
        self.assert_locations(pages, groups)

    def test_parent_child_limits_determinism_and_complete_coverage(self):
        text = "RESULTS\n\n" + "\n\n".join(("Memory cell retention improved at 90 °C. " * 35) + str(i) for i in range(12))
        pages = [page(1, text)]
        policy = ChunkPolicy(max_tokens=160, overlap_tokens=16, parent_max_tokens=400)
        groups = build_chunks(pages, "A retention study", policy)
        self.assertEqual(groups, build_chunks(pages, "A retention study", policy))
        self.assertGreater(len(groups), 1)
        covered = set()
        for group in groups:
            self.assertEqual(len(policy.splitter(400).chunks(group["text"])), 1)
            for child in group["children"]:
                self.assertEqual(len(policy.splitter(160).chunks(child["context_text"])), 1)
                for span in child["spans"]:
                    covered.update(range(span["start_offset"], span["end_offset"]))
        self.assertTrue(all(i in covered or c.isspace() for i, c in enumerate(text)))
        self.assert_locations(pages, groups)

    def test_repeated_margin_noise_only_removed_from_search_copy(self):
        pages = [page(i, f"CONFIDENTIAL RESEARCH HEADER\nMemory topic {i} has a measured value.\n{i}\n") for i in range(1, 4)]
        original = json.dumps(pages)
        groups = build_chunks(pages, "Memory", ChunkPolicy())
        self.assertNotIn("CONFIDENTIAL", " ".join(g["text"] for g in groups))
        self.assertEqual(json.dumps(pages), original)
        self.assert_locations(pages, groups)

    def test_local_tokenizer_disables_truncation_and_is_fingerprinted(self):
        tokenizer = {"version": "1.0", "truncation": {"direction": "Right", "max_length": 1, "strategy": "LongestFirst", "stride": 0},
                     "padding": None, "added_tokens": [], "normalizer": None, "pre_tokenizer": {"type": "Whitespace"},
                     "post_processor": None, "decoder": None,
                     "model": {"type": "WordLevel", "vocab": {"[UNK]": 0, "memory": 1, "cell": 2}, "unk_token": "[UNK]"}}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tokenizer.json"
            target.write_text(json.dumps(tokenizer), encoding="utf-8")
            env = Path(directory) / ".env"
            env.write_text(f'CHUNK_TOKENIZER_PATH="{target.as_posix()}"\nCHUNK_MAX_TOKENS=160\n', encoding="utf-8")
            policy = load_chunk_policy(env, environ={})
            self.assertTrue(policy.metadata()["tokenizer"].startswith("huggingface-local:"))
            groups = build_chunks([page(1, "memory cell " * 500)], "memory", policy)
            self.assertGreater(sum(len(g["children"]) for g in groups), 1)
            self.assertTrue(all(len(c["context_text"].split()) <= 160 for g in groups for c in g["children"]))
            with self.assertRaises(FileNotFoundError):
                load_chunk_policy(env, environ={"CHUNK_TOKENIZER_PATH": str(Path(directory) / "missing.json")})

    def test_invalid_budget_fails_explicitly(self):
        with self.assertRaises(ValueError):
            ChunkPolicy(max_tokens=128, overlap_tokens=100)


class ChunkIndexTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PilotTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_reindex_preserves_canonical_evidence_is_idempotent_and_exposes_context(self):
        from backend.pilot_curate import apply_curation
        target, _ = self.fixture.curation()
        apply_curation(target, self.fixture.db)
        tables = ["pages", "claims", "evidence_spans", "claim_evidence", "measurements", "condition_values", "knowledge_pages", "knowledge_links"]
        with connect(self.fixture.db) as db:
            before = {name: [tuple(r) for r in db.execute("SELECT * FROM " + name)] for name in tables}
            run = db.execute("SELECT current_run_id FROM paper_documents").fetchone()[0]
            result = reindex_document(db, run, ChunkPolicy(max_tokens=160, overlap_tokens=16))
            self.assertEqual(result["status"], "indexed")
            ids = [r[0] for r in db.execute("SELECT id FROM search_chunks")]
            self.assertEqual(reindex_document(db, run, ChunkPolicy(max_tokens=160, overlap_tokens=16))["status"], "unchanged")
            self.assertEqual(ids, [r[0] for r in db.execute("SELECT id FROM search_chunks")])
            self.assertEqual(before, {name: [tuple(r) for r in db.execute("SELECT * FROM " + name)] for name in tables})
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            db.commit()
        with patch.dict(os.environ, {"EVIDENCE_DB_PATH": str(self.fixture.db)}):
            hit = search("refresh", expert="dram", limit=8)["hits"][0]
            self.assertTrue(hit["source_spans"])
            self.assertIn("refresh", hit["parent_context"]["text"].lower())
            self.assertFalse(search("refresh", expert="nor", limit=8)["hits"])

    def test_failed_index_write_rolls_back_old_search(self):
        import sqlite3
        with connect(self.fixture.db) as db:
            before = [tuple(r) for r in db.execute("SELECT * FROM search_chunks")]
            fts = [tuple(r) for r in db.execute("SELECT * FROM chunk_fts")]
            run = db.execute("SELECT current_run_id FROM paper_documents").fetchone()[0]
            db.set_authorizer(lambda action, table, *_: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == "search_chunk_details" else sqlite3.SQLITE_OK)
            with self.assertRaises(sqlite3.DatabaseError):
                reindex_document(db, run, ChunkPolicy(max_tokens=160, overlap_tokens=16))
            db.set_authorizer(None)
            self.assertEqual(before, [tuple(r) for r in db.execute("SELECT * FROM search_chunks")])
            self.assertEqual(fts, [tuple(r) for r in db.execute("SELECT * FROM chunk_fts")])
