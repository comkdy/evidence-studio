"""Imports must publish search/demand without draining unrelated model jobs."""
import os
import runpy
import sys
import tempfile
import unittest
import warnings
from contextlib import redirect_stdout
from unittest.mock import patch

import test_pilot as fixtures
from backend.pilot_db import connect
from backend.wiki_engine import enqueue


class ImportWorkerBoundaryTests(unittest.TestCase):
    setUp=fixtures.PilotTests.setUp
    tearDown=fixtures.PilotTests.tearDown
    curation=fixtures.PilotTests.curation

    def run_import(self,module,arguments):
        with connect(self.db) as db:
            job=enqueue(db,'explicit administrator job',scope='common');db.commit()
        with patch.dict(os.environ,{'EVIDENCE_DB_PATH':str(self.db)}), \
             patch.object(sys,'argv',[module,*arguments,'--db',str(self.db)]), \
             patch('backend.wiki_engine.run_jobs',side_effect=AssertionError('import must not drain model work')), \
             tempfile.TemporaryFile(mode='w+',encoding='utf-8') as output, redirect_stdout(output),warnings.catch_warnings():
            warnings.simplefilter('ignore',RuntimeWarning)
            runpy.run_module(module,run_name='__main__')
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT state FROM wiki_jobs WHERE id=?',(job,)).fetchone()[0],'queued')
            self.assertTrue(db.execute("SELECT 1 FROM retrieval_units WHERE kind='passage'").fetchone())
            self.assertTrue(db.execute('SELECT 1 FROM knowledge_demands WHERE active=1').fetchone())

    def test_raw_import_returns_without_running_legacy_jobs(self):
        self.run_import('backend.pilot_ingest',['--raw',str(self.root)])

    def test_curated_evidence_import_returns_without_running_legacy_jobs(self):
        target,_=self.curation()
        self.run_import('backend.pilot_curate',[str(target)])
