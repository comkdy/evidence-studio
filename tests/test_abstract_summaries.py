import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_pilot as fixtures
from backend.abstract_summaries import PROMPT_VERSION, apply_summaries, source_fingerprint
from backend.pilot_api import paper, papers
from backend.pilot_db import connect, initialize
from backend.pilot_ingest import ingest_file


class AbstractSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "test.sqlite3"
        pdf = self.root / "source.pdf"
        initialize(self.db)
        fixtures.make_pdf(pdf)
        ingest_file(pdf, self.db, 0)
        with connect(self.db) as db:
            self.source = dict(db.execute("SELECT * FROM papers").fetchone())
        self.payload = {
            "producer": "test-model", "prompt_version": PROMPT_VERSION,
            "papers": [{"paper_id": self.source["id"], "summary_ko": "OCTRAM의 retention과 refresh를 다룹니다.",
                        "source_fingerprint": source_fingerprint(self.source["title"], self.source["abstract"])}],
        }
        self.file = self.root / "summaries.json"

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self):
        self.file.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
        return apply_summaries(self.file, self.db)

    def test_source_preservation_idempotence_and_same_summary_in_list_and_detail(self):
        self.assertEqual(self.apply(), {"written": 1, "unchanged": 0})
        with connect(self.db) as db:
            before = tuple(db.execute("SELECT * FROM paper_abstract_summaries").fetchone())
            self.assertEqual(dict(db.execute("SELECT * FROM papers").fetchone()), self.source)
        self.assertEqual(self.apply(), {"written": 0, "unchanged": 1})
        with connect(self.db) as db:
            self.assertEqual(tuple(db.execute("SELECT * FROM paper_abstract_summaries").fetchone()), before)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        with patch.dict(os.environ, {"EVIDENCE_DB_PATH": str(self.db)}):
            listing = papers(q="", expert="", limit=8, offset=0, facets="")["papers"][0]
            detail = paper(self.source["id"])
            self.assertEqual(listing["abstract_summary"], detail["abstract_summary"])
            self.assertEqual(detail["abstract_summary"]["text"], self.payload["papers"][0]["summary_ko"])
            self.assertEqual(detail["abstract"], self.source["abstract"])

    def test_missing_and_changed_sources_do_not_show_an_old_or_fabricated_summary(self):
        with patch.dict(os.environ, {"EVIDENCE_DB_PATH": str(self.db)}):
            self.assertIsNone(paper(self.source["id"])["abstract_summary"])
            self.apply()
            with connect(self.db) as db:
                db.execute("UPDATE papers SET abstract=abstract||' Revised.'")
                db.commit()
            self.assertIsNone(paper(self.source["id"])["abstract_summary"])
            with self.assertRaises(ValueError):
                self.apply()

    def test_invalid_batch_preserves_existing_summaries(self):
        self.apply()
        original = self.payload["papers"][0]["summary_ko"]
        self.payload["papers"][0]["summary_ko"] = "변경된 요약입니다."
        self.payload["papers"].append({**self.payload["papers"][0], "paper_id": "missing-paper"})
        with self.assertRaises(ValueError):
            self.apply()
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT summary_ko FROM paper_abstract_summaries").fetchone()[0], original)


if __name__ == "__main__":
    unittest.main()
