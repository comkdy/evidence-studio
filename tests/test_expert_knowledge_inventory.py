"""Expert configuration uses the same current shared Markdown as research QA."""
import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from pydantic import SecretStr

import test_pilot as fixtures
from backend.ai_settings import ModelSettings
from backend.expert_config import (
    ExpertCreate, ExpertDelete, ExpertDraft, ExpertRestore, ExpertUpdate,
    create_expert, delete_expert, expert_config, preview_membership,
    refresh_expert_policies, restore_expert, update_expert,
)
from backend.knowledge_inventory import reusable_common_wiki_ids
from backend.llm_client import CompanyLLM
from backend.pilot_curate import apply_curation
from backend.pilot_db import connect
from backend.pilot_ingest import ingest_file
from backend.wiki_engine import base_refs, merge_refs, reconcile, snapshot
from backend.wiki_store import WikiStore, digest


class ExpertKnowledgeInventoryTests(unittest.TestCase):
    curation = fixtures.PilotTests.curation

    def setUp(self):
        fixtures.PilotTests.setUp(self)
        self.env = patch.dict(os.environ, {
            'EVIDENCE_DB_PATH': str(self.db), 'AUTH_MODE': 'local_admin',
            'LLM_BASE_URL': '', 'LLM_API_KEY': '', 'LLM_BASE_MODEL': '',
            'EMBEDDING_BASE_URL': '', 'EMBEDDING_API_KEY': '',
            'EMBEDDING_MODEL': '', 'RERANKER_BASE_URL': '',
        })
        self.env.start()
        target, _ = self.curation()
        apply_curation(target, self.db)
        self.store = WikiStore(self.db)
        self.no_llm = CompanyLLM(ModelSettings('', '', SecretStr('')))
        with connect(self.db) as db:
            self.paper_id = db.execute('SELECT id FROM papers').fetchone()[0]

    def tearDown(self):
        self.env.stop()
        fixtures.PilotTests.tearDown(self)

    def sync(self):
        reconcile(self.db, self.no_llm)

    def draft(self, **changes):
        return ExpertDraft(name='Focused expert', overview='Existing evidence',
                           facets={'platform': []}, included_ids=[self.paper_id], **changes)

    def inventory(self, ids=None):
        with connect(self.db) as db:
            return reusable_common_wiki_ids(db, [self.paper_id] if ids is None else ids)

    def test_preview_switches_from_legacy_notes_to_current_markdown(self):
        self.assertEqual(self.inventory(), ['w1'])
        self.sync()
        expected = sorted(r['id'] for r in self.store.rows()
                          if r['scope'] == 'common' and r['kind'] in ('source', 'topic'))
        self.assertEqual(len(expected), 2)
        self.assertEqual(self.inventory(), expected)
        with connect(self.db) as db:
            preview = preview_membership(db, self.draft())
        self.assertEqual(preview['counts'], {
            'papers': 1, 'claims': 1, 'measurements': 1, 'wikis': 2, 'review': 0,
        })
        # The deterministic index is present, but adds no knowledge document.
        self.assertTrue(any(r['kind'] == 'expert_index' for r in self.store.rows()))

    def test_inventory_ignores_specializations_parts_and_cross_paper_topics(self):
        self.sync()
        expected = self.inventory()
        state = snapshot(self.db)
        source = next(r for r in self.store.rows() if r['kind'] == 'source')
        own_refs = merge_refs([source], state, 'dram')
        self.store.publish('focused', 'experts/dram/focused.md', 'Focused', 'A note',
                           own_refs, digest(own_refs), scope='expert', expert_id='dram',
                           kind='specialization')
        shared_refs = json.loads(source['refs_json'])
        self.store.publish('part', 'common/part.md', 'Part', 'One part', shared_refs,
                           digest(['part', shared_refs]), kind='source_part')
        self.assertEqual(self.inventory(), expected)

        pdf = self.root / 'second.pdf'
        fixtures.make_pdf(pdf, fixtures.TEXT.replace('DRAM Retention Study', 'NAND Study'))
        ingest_file(pdf, self.db, 0)
        state = snapshot(self.db)
        refs = base_refs(state['papers'], state)
        self.store.publish('cross-paper', 'common/cross.md', 'Cross paper', 'Both papers',
                           refs, digest(refs), kind='topic')
        self.assertEqual(self.inventory(), expected)
        self.assertIn('cross-paper', self.inventory(state['papers']))

    def test_same_transaction_source_changes_hide_stale_wikis(self):
        self.sync()
        expected = self.inventory()
        with connect(self.db) as db:
            db.execute("UPDATE claims SET summary='Changed finding' WHERE id='claim1'")
            self.assertEqual(reusable_common_wiki_ids(db, [self.paper_id]), [])
            self.assertEqual(preview_membership(db, self.draft())['counts']['wikis'], 0)
            db.rollback()
        self.assertEqual(self.inventory(), expected)

    def test_archived_dependency_does_not_fall_back_to_legacy_count(self):
        self.sync()
        source = next(r for r in self.store.rows() if r['kind'] == 'source')
        with connect(self.db) as db:
            db.execute("UPDATE wiki_files SET status='archived' WHERE id=?", (source['id'],))
            db.commit()
        self.assertEqual(self.inventory(), [])

    def test_edited_or_missing_markdown_and_dependents_are_not_reusable(self):
        self.sync()
        source = next(r for r in self.store.rows() if r['kind'] == 'source')
        topic = next(r for r in self.store.rows() if r['kind'] == 'topic')
        path = self.store.root / source['path']
        original = path.read_text(encoding='utf-8')
        path.write_text(original + '\nUnadopted edit\n', encoding='utf-8')
        self.assertEqual(self.inventory(), [])
        path.write_text(original, encoding='utf-8')
        self.assertEqual(len(self.inventory()), 2)
        (self.store.root / topic['path']).unlink()
        self.assertEqual(self.inventory(), [source['id']])
        path.unlink()
        self.assertEqual(self.inventory(), [])

    def test_create_disable_edit_delete_restore_keeps_one_shared_inventory(self):
        self.sync()
        wiki_hashes = {r['id']: r['content_hash'] for r in self.store.rows()}
        created = create_expert(ExpertCreate(request_id=uuid4(), **self.draft().model_dump()))
        expert_id = created['id']
        self.assertEqual(created['counts']['wikis'], 2)
        self.assertEqual(snapshot(self.db)['experts'][expert_id]['papers'], [self.paper_id])

        config = expert_config(expert_id)
        disabled = dict(config['draft'], enabled=False)
        result = update_expert(expert_id, ExpertUpdate(revision=config['revision'], **disabled))
        self.assertEqual(result['counts']['wikis'], 2)
        self.assertNotIn(expert_id, snapshot(self.db)['experts'])
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT paper_id FROM expert_papers WHERE expert_id=?',
                                        (expert_id,)).fetchone()[0], self.paper_id)
            # Bulk membership maintenance must not rebuild every Wiki inventory.
            with patch('backend.expert_config.reusable_common_wiki_ids',
                       side_effect=AssertionError('unneeded inventory scan')):
                refresh_expert_policies(db)
            db.commit()

        config = expert_config(expert_id)
        empty = dict(config['draft'], enabled=True, included_ids=[])
        result = update_expert(expert_id, ExpertUpdate(revision=config['revision'], **empty))
        self.assertEqual(result['counts']['papers'], 0)
        self.assertEqual(result['counts']['wikis'], 0)
        self.assertEqual(snapshot(self.db)['experts'][expert_id]['papers'], [])
        config = expert_config(expert_id)
        restored_scope = dict(config['draft'], included_ids=[self.paper_id])
        update_expert(expert_id, ExpertUpdate(revision=config['revision'], **restored_scope))
        deleted = delete_expert(expert_id, ExpertDelete(revision=expert_config(expert_id)['revision']))
        self.assertNotIn(expert_id, snapshot(self.db)['experts'])
        restore_expert(expert_id, ExpertRestore(deleted_at=deleted['deleted_at']))
        self.assertEqual(snapshot(self.db)['experts'][expert_id]['papers'], [self.paper_id])
        self.assertEqual(wiki_hashes, {r['id']: r['content_hash'] for r in self.store.rows()})
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM claims').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM measurements').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
