"""Live expert scopes and derived Wiki lifecycle, using isolated fixture data."""
import json
from uuid import uuid4
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException
import test_markdown_wiki as wiki_fixtures
import test_pilot as pilot_fixtures
from backend.expert_config import (ExpertCreate, ExpertUpdate, ExpertDelete, ExpertRestore,
    create_expert, update_expert, delete_expert, restore_expert, expert_config)
from backend.pilot_db import connect
from backend.wiki_engine import reconcile, snapshot, _full_snapshot, base_refs, current, CONTENT_KINDS
from backend.wiki_api import list_wikis, load_catalogue, request_topic, TopicRequest, recommendations
from backend.wiki_retrieval import retrieve, reindex


class WikiLifecycleTests(TestCase):
    curation=pilot_fixtures.PilotTests.curation
    setUp=wiki_fixtures.MarkdownWikiTests.setUp
    tearDown=wiki_fixtures.MarkdownWikiTests.tearDown

    def new_expert(self):
        with connect(self.db) as db:
            pid=db.execute('SELECT paper_id FROM claims WHERE id=?',('claim1',)).fetchone()[0]
        return create_expert(ExpertCreate(request_id=uuid4(),name='Retention Research',
            overview='retention and reliability research',facets={'platform':[]},included_ids=[pid]))

    def change(self,eid,**changes):
        config=expert_config(eid)
        return update_expert(eid,ExpertUpdate(**(config['draft']|changes),revision=config['revision']))

    def test_created_expert_reuses_common_content_before_index_job(self):
        llm=wiki_fixtures.FakeLLM();reconcile(self.db,llm)
        common={row['id']:row['content_hash'] for row in self.store.rows() if row['scope']=='common'}
        created=self.new_expert();eid=created['id']
        self.assertIsNone(self.store.record('expert-index-'+eid))
        listed=list_wikis(expert=eid,limit=100)
        self.assertEqual(listed['total'],len(common))
        self.assertTrue(retrieve('retention',[eid],self.db)[0])
        with connect(self.db) as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM wiki_jobs WHERE state='queued'").fetchone())
        llm.calls.clear();reconcile(self.db,llm)
        self.assertEqual(llm.calls,[])
        self.assertEqual(common,{row['id']:row['content_hash'] for row in self.store.rows() if row['scope']=='common'})
        self.assertIsNotNone(self.store.record('expert-index-'+eid))
        self.assertEqual(list_wikis(expert=eid,limit=100)['total'],listed['total'])

    def test_disabled_expert_is_excluded_immediately_then_reenabled(self):
        reconcile(self.db,self.no_llm)
        eid=self.new_expert()['id'];reconcile(self.db,self.no_llm)
        source=next(row for row in self.store.rows() if row['kind']=='source')
        note=request_topic(eid,TopicRequest(title='Retention experiments',source_ids=[source['id']]))
        reconcile(self.db,self.no_llm)
        self.assertTrue(list_wikis(expert=eid)['wikis'])
        self.change(eid,enabled=False)
        self.assertEqual(list_wikis(expert=eid)['total'],0)
        self.assertFalse(retrieve('retention',[eid],self.db)[0])
        union,_=retrieve('retention',['dram',eid],self.db)
        self.assertFalse(any(unit['expert_id']==eid for unit in union))
        with self.assertRaises(HTTPException):
            request_topic(eid,TopicRequest(title='Unavailable',source_ids=[source['id']]))
        reconcile(self.db,self.no_llm)
        self.assertEqual(self.store.record(note['id'])['status'],'archived')
        self.change(eid,enabled=True)
        self.assertGreater(list_wikis(expert=eid)['total'],0)
        reconcile(self.db,self.no_llm)
        self.assertNotEqual(self.store.record(note['id'])['status'],'archived')

    def test_delete_restore_preserves_common_files_and_specialization_identity(self):
        reconcile(self.db,self.no_llm)
        eid=self.new_expert()['id'];reconcile(self.db,self.no_llm)
        source=next(row for row in self.store.rows() if row['kind']=='source')
        note=request_topic(eid,TopicRequest(title='Retention interpretation',source_ids=[source['id']]))
        reconcile(self.db,self.no_llm)
        common={row['id']:row['content_hash'] for row in self.store.rows() if row['scope']=='common'}
        removed=delete_expert(eid,ExpertDelete(revision=expert_config(eid)['revision']))
        self.assertEqual(list_wikis(expert=eid)['total'],0)
        reconcile(self.db,self.no_llm)
        archived=self.store.record(note['id'])
        self.assertEqual(archived['status'],'archived')
        self.assertTrue((self.store.root/archived['path']).is_file())
        restore_expert(eid,ExpertRestore(deleted_at=removed['deleted_at']))
        self.assertGreater(list_wikis(expert=eid)['total'],0)
        reconcile(self.db,self.no_llm)
        self.assertNotEqual(self.store.record(note['id'])['status'],'archived')
        self.assertEqual(common,{row['id']:row['content_hash'] for row in self.store.rows() if row['scope']=='common'})

    def test_membership_edit_hides_previous_scope_before_worker(self):
        reconcile(self.db,self.no_llm)
        eid=self.new_expert()['id'];reconcile(self.db,self.no_llm)
        self.change(eid,included_ids=[])
        self.assertEqual(list_wikis(expert=eid)['total'],0)
        self.assertFalse(retrieve('retention',[eid],self.db)[0])
        self.assertNotIn('expert-index-'+eid,load_catalogue()[2])
        self.assertGreater(list_wikis(expert='dram')['total'],0)

    def test_internal_pages_and_indexes_are_not_content_or_topic_request_inputs(self):
        reconcile(self.db,self.no_llm)
        state=snapshot(self.db);pid=next(iter(state['papers']))
        internals=[]
        for kind in ('source_part','topic_part'):
            internals.append(self.store.publish('internal-'+kind,'common/'+kind+'.md',kind,
                '# Internal retention',base_refs([pid],state),kind,kind=kind))
        listed=list_wikis(expert='dram',limit=100)
        self.assertTrue(all(row['kind'] in CONTENT_KINDS for row in listed['wikis']))
        self.assertEqual(listed['total'],2)
        for row in internals:
            with self.assertRaises(HTTPException) as error:
                request_topic('dram',TopicRequest(title='Invalid internal dependency',source_ids=[row['id']]))
            self.assertEqual(error.exception.status_code,422)
        reindex(self.db)
        with connect(self.db) as db:
            indexed={r[0] for r in db.execute("SELECT reference_id FROM retrieval_units WHERE kind='wiki'")}
        self.assertTrue(all(row['id'] not in indexed for row in internals))
        self.assertNotIn('expert-index-dram',indexed)

    def test_archived_dependency_invalidates_parent_before_reindex(self):
        reconcile(self.db,self.no_llm)
        source=next(row for row in self.store.rows() if row['kind']=='source')
        with connect(self.db) as db:
            db.execute("UPDATE wiki_files SET status='archived' WHERE id=?",(source['id'],));db.commit()
        state=snapshot(self.db);catalogue={row['id']:row for row in self.store.rows()}
        topic=next(row for row in catalogue.values() if row['kind']=='topic')
        self.assertFalse(current(topic,state,catalogue))
        self.assertNotIn(topic['id'],load_catalogue()[2])
        self.assertEqual(list_wikis(expert='dram')['total'],0)
        ranked,_=retrieve('retention',['dram'],self.db)
        self.assertFalse(any(row['reference_id']==source['id'] for row in ranked))

    def test_snapshot_can_read_uncommitted_transaction_without_closing_it(self):
        before=_full_snapshot(self.db)
        with connect(self.db) as db:
            db.execute("UPDATE papers SET abstract=abstract||' Added evidence.'")
            same_transaction=_full_snapshot(connection=db)
            external=_full_snapshot(self.db)
            self.assertNotEqual(same_transaction['papers'],before['papers'])
            self.assertEqual(external['papers'],before['papers'])
            self.assertEqual(db.execute('SELECT count(*) FROM papers').fetchone()[0],1)
            db.rollback()

    def test_recommendations_share_korean_glossary_with_retrieval(self):
        reconcile(self.db,self.no_llm)
        created=self.new_expert()
        recommendations_result=recommendations('신뢰성 연구',self.no_llm)
        self.assertIn('reliability',recommendations_result['terms'])
        self.assertIn(created['id'],[item['id'] for item in recommendations_result['experts']])
