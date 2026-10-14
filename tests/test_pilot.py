import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from backend.pilot_db import initialize, connect, sync_wiki_memberships
from backend.pilot_ingest import ingest_file, sha256_file
from backend.pilot_curate import apply_curation, locate_quote
from backend.pilot_api import search, wikis, wiki_detail, recommend

TEXT = "DRAM Retention Study\n\nJ. Author, K. Scientist\nUniversity\nAbstract - OCTRAM retention exceeded 100 seconds at 90 C in the tested array. The experiment compared refresh timing and measured current in DRAM.\nINTRODUCTION\nOCTRAM retention exceeded 100 seconds at 90 C in the tested array. The refresh model estimated 74 percent power reduction."

def make_pdf(path,text=TEXT):
    writer=PdfWriter();page=writer.add_blank_page(width=612,height=792)
    font=DictionaryObject({NameObject("/Type"):NameObject("/Font"),NameObject("/Subtype"):NameObject("/Type1"),NameObject("/BaseFont"):NameObject("/Helvetica")})
    page[NameObject("/Resources")]=DictionaryObject({NameObject("/Font"):DictionaryObject({NameObject("/F1"):writer._add_object(font)})})
    parts=["BT /F1 12 Tf 36 750 Td"]
    for line in text.splitlines():
        line=line.replace("\\","\\\\").replace("(","\\(").replace(")","\\)")
        parts.extend(["("+line+") Tj","0 -16 Td"])
    parts.append("ET")
    stream=DecodedStreamObject();stream.set_data("\n".join(parts).encode("ascii"))
    page[NameObject("/Contents")]=writer._add_object(stream)
    with open(path,"wb") as f: writer.write(f)

class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.db=self.root/"pilot.sqlite3";self.pdf=self.root/"real.pdf"
        initialize(self.db);make_pdf(self.pdf)
        self.assertEqual(ingest_file(self.pdf,self.db,0)["status"],"ready")
    def tearDown(self): self.tmp.cleanup()
    def curation(self):
        with connect(self.db) as db:
            text=db.execute("SELECT text FROM pages").fetchone()[0]
        quote="The refresh model estimated 74 percent power reduction."
        self.assertIn(quote,text)
        src={"page":1,"quote":quote,"locator":"본문"}
        payload={"papers":[{"filename":"real.pdf","sha256":sha256_file(self.pdf),"experts":["dram"],"claims":[{"id":"claim1","summary":"Refresh model estimates 74 percent reduction.","kind":"estimate","scope":"Model estimate only","sources":[src],"measurements":[{"id":"m1","label":"Power reduction","raw_value":"74%","operator":"=","numeric_value":74,"unit":"%","sample":"Model","kind":"estimated","conditions":[{"name":"Temperature","raw_value":None}]}]}]}],"wikis":[{"id":"w1","expert_id":"dram","title":"Refresh estimate","body":"Estimated reduction","limitation":"Not measured","claim_ids":["claim1"]}]}
        target=self.root/"curation.json";target.write_text(json.dumps(payload),encoding="utf-8")
        return target,payload
    def test_duplicate_and_changed_path_preserve_canonical_document(self):
        self.assertEqual(ingest_file(self.pdf,self.db,0)["status"],"unchanged")
        duplicate=self.root/"copy.pdf";duplicate.write_bytes(self.pdf.read_bytes())
        self.assertEqual(ingest_file(duplicate,self.db,0)["status"],"unchanged")
        make_pdf(self.pdf,TEXT+" New text.")
        self.assertEqual(ingest_file(self.pdf,self.db,0)["status"],"needs_review")
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM papers").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM document_sources").fetchone()[0],2)
            self.assertNotIn("New text.",db.execute("SELECT text FROM pages").fetchone()[0])
    def test_curation_links_exact_source_and_is_idempotent(self):
        target,_=self.curation()
        self.assertEqual(apply_curation(target,self.db)["status"],"created")
        self.assertEqual(apply_curation(target,self.db)["status"],"unchanged")
        with connect(self.db) as db:
            span=db.execute("SELECT e.*,p.text FROM evidence_spans e JOIN pages p ON p.id=e.page_id").fetchone()
            self.assertEqual(span["quote"],span["text"][span["start_offset"]:span["end_offset"]])
            self.assertEqual(db.execute("SELECT count(*) FROM claim_measurements").fetchone()[0],1)
            self.assertIsNone(db.execute("SELECT raw_value FROM condition_values").fetchone()[0])
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
    def test_missing_source_rolls_back_whole_curation(self):
        target,payload=self.curation()
        payload["papers"][0]["claims"][0]["sources"][0]["quote"]="invented source 999"
        target.write_text(json.dumps(payload),encoding="utf-8")
        with self.assertRaises(ValueError): apply_curation(target,self.db)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM curation_runs").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT count(*) FROM claims").fetchone()[0],0)
    def test_search_is_real_and_respects_expert_boundary(self):
        target,_=self.curation();apply_curation(target,self.db)
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db)}):
            result=search("refresh 74",expert="dram",limit=8)
            self.assertEqual(len(result["claims"]),1)
            self.assertEqual(result["claims"][0]["measurements"][0]["raw_value"],"74%")
            self.assertTrue(result["hits"])
            self.assertFalse(search("refresh 74",expert="nor",limit=8)["hits"])
            self.assertFalse(search("refresh 74",expert="nor",limit=8)["claims"])
            self.assertFalse(search('"; DROP TABLE papers; --',expert="",limit=8)["hits"])
    def test_cross_paper_link_and_out_of_scope_wiki_are_rejected(self):
        target,payload=self.curation();apply_curation(target,self.db)
        with connect(self.db) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO claim_measurements VALUES('claim1','m1','wrong-paper','support')")
        payload["papers"]=[];payload["wikis"][0]["id"]="outside";payload["wikis"][0]["expert_id"]="nor"
        target.write_text(json.dumps(payload),encoding="utf-8")
        with self.assertRaises(ValueError): apply_curation(target,self.db)

    def test_quote_matching_never_changes_numeric_values(self):
        self.assertEqual(locate_quote("x\nvalue > 100 s here","value > 100 s"),(2,15))
        with self.assertRaises(ValueError): locate_quote("value > 100 s","value = 100 s")
        with self.assertRaises(ValueError): locate_quote("same same","same")

    def test_multiple_experts_are_a_union_without_widening_to_unknown_scope(self):
        target,_=self.curation();apply_curation(target,self.db)
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db)}):
            result=search("refresh 74",expert="nor,dram",limit=8)
            self.assertEqual([c["id"] for c in result["claims"]],["claim1"])
            self.assertTrue(result["hits"])
            self.assertEqual(search("refresh 74",expert="nor,missing",limit=8)["claims"],[])
            self.assertEqual(search("질문",expert="dram",limit=8)["total_claims"],0)
            self.assertEqual(recommend("refresh")["experts"][0]["id"],"dram")

    def test_wiki_lists_do_not_load_all_evidence_and_detail_obeys_pagination(self):
        target,_=self.curation();apply_curation(target,self.db)
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db)}):
            listed=wikis(expert="dram",q="",limit=1,offset=0)
            self.assertEqual(listed["total"],1)
            self.assertNotIn("claims",listed["wikis"][0])
            self.assertEqual(listed["wikis"][0]["claim_count"],1)
            self.assertEqual(wiki_detail("w1",limit=1,offset=1)["claims"],[])
            self.assertEqual(wiki_detail("w1",limit=1,offset=0)["claims"][0]["id"],"claim1")
            self.assertEqual(wikis(expert="nor",q="",limit=1,offset=0)["total"],0)

    def test_title_correction_retains_previous_value_source_and_search_index(self):
        target,payload=self.curation()
        payload["papers"][0]["corrected_title"]="Corrected Memory Title"
        payload["papers"][0]["title_source_quote"]={"page":1,"quote":"DRAM Retention Study","locator":"Title"}
        target.write_text(json.dumps(payload),encoding="utf-8")
        apply_curation(target,self.db)
        with connect(self.db) as db:
            audit=db.execute("SELECT * FROM paper_metadata_changes").fetchone()
            self.assertNotEqual(audit["previous_value"],audit["corrected_value"])
            self.assertEqual(db.execute("SELECT title FROM papers").fetchone()[0],"Corrected Memory Title")
            self.assertTrue(db.execute("SELECT 1 FROM chunk_fts WHERE chunk_fts MATCH 'corrected'").fetchone())
            self.assertEqual(db.execute("SELECT quote FROM evidence_spans WHERE id=?",(audit["source_span_id"],)).fetchone()[0],"DRAM Retention Study")

    def test_shared_wiki_is_reused_only_while_all_sources_belong_to_expert(self):
        target,_=self.curation();apply_curation(target,self.db)
        with connect(self.db) as db:
            paper_id=db.execute("SELECT paper_id FROM claims WHERE id='claim1'").fetchone()[0]
            db.execute("INSERT INTO expert_papers VALUES('modeling',?,'reviewed')",(paper_id,))
            sync_wiki_memberships(db);db.commit()
            self.assertEqual(db.execute("SELECT count(*) FROM knowledge_pages").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM expert_knowledge_pages").fetchone()[0],2)
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db)}):
            self.assertEqual(wikis(expert="modeling",q="",limit=6,offset=0)["total"],1)
        with connect(self.db) as db:
            db.execute("DELETE FROM expert_papers WHERE expert_id='modeling'")
            sync_wiki_memberships(db);db.commit()
            self.assertEqual(db.execute("SELECT count(*) FROM expert_knowledge_pages").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM knowledge_pages").fetchone()[0],1)


    def test_created_expert_persists_and_reuses_existing_evidence(self):
        from uuid import uuid4
        from backend.expert_config import ExpertCreate, create_expert
        target,_=self.curation();apply_curation(target,self.db)
        draft=ExpertCreate(request_id=uuid4(),name="DRAM Reliability",overview="Retained evidence",facets={"platform":["DRAM"],"focus":["Reliability"]})
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db),"AUTH_MODE":"local_admin"}):
            created=create_expert(draft)
            self.assertEqual(created["counts"]["papers"],1)
            self.assertEqual(created["counts"]["claims"],1)
            self.assertEqual(created["counts"]["wikis"],1)
            self.assertEqual(create_expert(draft)["status"],"existing")
            self.assertEqual(wikis(expert=created["id"],q="",limit=6,offset=0)["total"],1)
            self.assertEqual(search("refresh 74",expert=created["id"],limit=8)["total_claims"],1)
        initialize(self.db)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM claims").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM knowledge_pages").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM expert_policies").fetchone()[0],1)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_expert_facets_intersect_axes_and_keep_ambiguous_papers_for_review(self):
        from backend.expert_config import ExpertDraft, preview_membership, classify_cached
        with connect(self.db) as db:
            def preview(facets,**kw):
                return preview_membership(db,ExpertDraft(name="Test",overview="Test",facets=facets,**kw))
            self.assertEqual(preview({"platform":["DRAM","NAND"],"focus":["Reliability"]})["counts"]["papers"],1)
            self.assertEqual(preview({"platform":["DRAM"],"focus":["Scaling"]})["counts"]["papers"],0)
            self.assertEqual(preview({})["counts"]["papers"],1)
            self.assertEqual(preview({"platform":[]})["counts"]["papers"],0)
            unknown=preview({"platform":["DRAM"],"application":["CAM"]})
            self.assertEqual(unknown["counts"]["papers"],0)
            self.assertEqual(unknown["counts"]["review"],1)
            paper_id=unknown["review"][0]["id"]
            self.assertEqual(preview({"platform":[]},included_ids=[paper_id])["counts"]["papers"],1)
            self.assertEqual(preview({},excluded_ids=[paper_id])["counts"]["papers"],0)
            paper=dict(db.execute("SELECT id,title,abstract FROM papers").fetchone())
            paper["title"]="Monolithic 3D SRAMs using Thin-Film Transistors"
            self.assertEqual(classify_cached(db,paper)["platform"],["SRAM"])

    def test_expert_validation_and_server_role_prevent_invalid_creation(self):
        from uuid import uuid4
        from fastapi import HTTPException
        from pydantic import ValidationError
        from backend.expert_config import ExpertDraft, ExpertCreate, create_expert, expert_preview
        with self.assertRaises(ValidationError):
            ExpertDraft(name="x",overview="x",facets={"platform":["invented"]})
        draft=ExpertCreate(request_id=uuid4(),name="New expert",overview="Description")
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db),"AUTH_MODE":"local_admin"}), patch("backend.auth.current_user",return_value={"employee_id":"test-user","role":"user","active":True}):
            with self.assertRaises(HTTPException) as error:create_expert(draft)
            self.assertEqual(error.exception.status_code,403)
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db),"AUTH_MODE":"local_admin"}):
            with self.assertRaises(HTTPException):
                expert_preview(ExpertDraft(name="x",overview="x",included_ids=["missing"]))
            create_expert(draft)
            with self.assertRaises(HTTPException) as duplicate:
                create_expert(ExpertCreate(request_id=uuid4(),name="new expert",overview="x"))
            self.assertEqual(duplicate.exception.status_code,409)

    def test_new_ingested_papers_join_saved_expert_and_disabled_experts_cannot_answer(self):
        from uuid import uuid4
        from backend.expert_config import ExpertCreate, create_expert
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db),"AUTH_MODE":"local_admin"}):
            created=create_expert(ExpertCreate(request_id=uuid4(),name="Disabled DRAM",overview="x",facets={"platform":["DRAM"]},enabled=False))
            new_pdf=self.root/"new.pdf"
            make_pdf(new_pdf,TEXT.replace("Study","Reliability Experiment").replace("100 seconds","120 seconds"))
            self.assertEqual(ingest_file(new_pdf,self.db,0)["status"],"ready")
            with connect(self.db) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM expert_papers WHERE expert_id=?",(created["id"],)).fetchone()[0],2)
            self.assertNotIn(created["id"],[e["id"] for e in recommend("refresh")["experts"]])
            self.assertEqual(search("refresh",expert=created["id"],limit=8)["hits"],[])

    def test_library_category_filters_counts_and_pagination_use_same_classifications(self):
        from backend.pilot_api import papers
        other=self.root/"nand.pdf"
        make_pdf(other,TEXT.replace("DRAM","NAND").replace("OCTRAM","NAND").replace("Retention Study","Scaling Study").replace("retention","scaling").replace("refresh","scaling"))
        ingest_file(other,self.db,0)
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db)}):
            selected=papers(q="",expert="",limit=1,offset=0,facets=json.dumps({"platform":["DRAM","NAND"],"focus":["Reliability"]}))
            self.assertEqual(selected["total"],1)
            self.assertEqual(selected["papers"][0]["facets"]["platform"],["DRAM"])
            self.assertEqual(selected["facet_counts"]["platform"]["DRAM"],1)
            self.assertEqual(selected["facet_counts"]["platform"]["NAND"],0)
            both=papers(q="",expert="",limit=1,offset=1,facets=json.dumps({"platform":["DRAM","NAND"]}))
            self.assertEqual(both["total"],2)
            self.assertEqual(len(both["papers"]),1)
            unknown=papers(q="",expert="",limit=8,offset=0,facets=json.dumps({"application":["__unclassified__"]}))
            self.assertEqual(unknown["total"],2)

    def test_expert_settings_round_trip_and_update_refresh_knowledge_scope(self):
        from uuid import uuid4
        from backend.expert_config import ExpertCreate, ExpertUpdate, create_expert, expert_config, update_expert
        target,_=self.curation();apply_curation(target,self.db)
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db),"AUTH_MODE":"local_admin"}):
            created=create_expert(ExpertCreate(request_id=uuid4(),name="Custom DRAM",overview="Original",facets={"platform":["DRAM"],"focus":["Reliability"]}))
            loaded=expert_config(created["id"])
            self.assertFalse(loaded["legacy"])
            self.assertEqual(loaded["draft"]["facets"]["platform"],["DRAM"])
            self.assertEqual(loaded["draft"]["facets"]["focus"],["Reliability"])
            update={**loaded["draft"],"name":"Renamed NAND","overview":"Changed overview","enabled":False,"revision":loaded["revision"]}
            update["facets"]={**update["facets"],"platform":["NAND"]}
            saved=update_expert(created["id"],ExpertUpdate(**update))
            self.assertEqual(saved["counts"]["papers"],0)
            reopened=expert_config(created["id"])
            self.assertEqual(reopened["draft"]["name"],"Renamed NAND")
            self.assertEqual(reopened["draft"]["overview"],"Changed overview")
            self.assertEqual(reopened["draft"]["facets"]["platform"],["NAND"])
            self.assertFalse(reopened["draft"]["enabled"])
            self.assertEqual(wikis(expert=created["id"],q="",limit=6,offset=0)["total"],0)
            self.assertEqual(search("refresh",expert=created["id"],limit=8)["total_claims"],0)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM claims").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM knowledge_pages").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM expert_policies").fetchone()[0],1)

    def test_legacy_expert_edit_preserves_reviewed_memberships_without_inventing_facets(self):
        from backend.expert_config import ExpertUpdate, expert_config, update_expert, refresh_expert_policies
        target,_=self.curation();apply_curation(target,self.db)
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db),"AUTH_MODE":"local_admin"}):
            loaded=expert_config("dram")
            self.assertTrue(loaded["legacy"])
            self.assertEqual(loaded["draft"]["facets"]["platform"],[])
            self.assertEqual(len(loaded["draft"]["included_ids"]),1)
            with connect(self.db) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM expert_policies").fetchone()[0],0)
            update_expert("dram",ExpertUpdate(**{**loaded["draft"],"overview":"Reviewed DRAM overview","revision":loaded["revision"]}))
            initialize(self.db)
            with connect(self.db) as db:
                refresh_expert_policies(db);db.commit()
            reopened=expert_config("dram")
            self.assertFalse(reopened["legacy"])
            self.assertEqual(reopened["draft"]["included_ids"],loaded["draft"]["included_ids"])
            self.assertEqual(reopened["draft"]["overview"],"Reviewed DRAM overview")
            self.assertEqual(wikis(expert="dram",q="",limit=6,offset=0)["total"],1)
            self.assertEqual(search("refresh",expert="dram",limit=8)["total_claims"],1)

    def test_expert_edit_rejects_stale_conflicting_and_unauthorized_changes(self):
        from fastapi import HTTPException
        from backend.expert_config import ExpertUpdate, expert_config, update_expert
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db),"AUTH_MODE":"local_admin"}):
            loaded=expert_config("dram")
            draft=ExpertUpdate(**loaded["draft"],revision=loaded["revision"])
            update_expert("dram",draft)
            with self.assertRaises(HTTPException) as stale:update_expert("dram",draft)
            self.assertEqual(stale.exception.status_code,409)
            latest=expert_config("dram")
            with self.assertRaises(HTTPException) as duplicate:
                update_expert("dram",ExpertUpdate(**{**latest["draft"],"name":"nor flash","revision":latest["revision"]}))
            self.assertEqual(duplicate.exception.status_code,409)
            with self.assertRaises(HTTPException) as missing:expert_config("missing")
            self.assertEqual(missing.exception.status_code,404)
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db),"AUTH_MODE":"local_admin"}), patch("backend.auth.current_user",return_value={"employee_id":"test-user","role":"user","active":True}):
            for action in (lambda:expert_config("dram"),lambda:update_expert("dram",draft)):
                with self.assertRaises(HTTPException) as forbidden:action()
                self.assertEqual(forbidden.exception.status_code,403)

    def test_expert_deletion_hides_scope_but_preserves_shared_evidence_and_wiki(self):
        from backend.expert_config import ExpertDelete, delete_expert, expert_config, refresh_expert_policies
        from backend.pilot_api import summary, papers
        target,_=self.curation();apply_curation(target,self.db)
        with connect(self.db) as db:
            paper_id=db.execute("SELECT paper_id FROM claims WHERE id='claim1'").fetchone()[0]
            db.execute("INSERT INTO expert_papers VALUES('modeling',?,'reviewed')",(paper_id,))
            sync_wiki_memberships(db);db.commit()
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db),"AUTH_MODE":"local_admin"}):
            config=expert_config("dram")
            delete_expert("dram",ExpertDelete(revision=config["revision"]))
            initialize(self.db)
            with connect(self.db) as db:refresh_expert_policies(db);db.commit()
            self.assertNotIn("dram",[e["id"] for e in summary()["experts"]])
            self.assertNotIn("dram",[e["id"] for e in recommend("refresh")["experts"]])
            self.assertEqual(search("refresh",expert="dram",limit=8)["total_claims"],0)
            self.assertEqual(papers(q="",expert="dram",limit=8,offset=0)["total"],0)
            self.assertEqual(wikis(expert="dram",q="",limit=6,offset=0)["total"],0)
            self.assertEqual(wikis(expert="modeling",q="",limit=6,offset=0)["total"],1)
            self.assertEqual(wiki_detail("w1",limit=3,offset=0)["total_claims"],1)
            self.assertNotIn("dram",[e["id"] for e in wiki_detail("w1",limit=3,offset=0)["claims"][0]["experts"]])
            new_pdf=self.root/"new-dram.pdf";make_pdf(new_pdf,TEXT.replace("Study","New Study"))
            ingest_file(new_pdf,self.db,0)
            self.assertNotIn("dram",[e["id"] for e in summary()["experts"]])
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM claims").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM measurements").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM knowledge_pages").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM expert_papers WHERE expert_id='dram'").fetchone()[0],1)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_expert_delete_undo_and_same_name_recreation(self):
        from uuid import uuid4
        from fastapi import HTTPException
        from backend.expert_config import ExpertCreate, ExpertDelete, ExpertRestore, create_expert, delete_expert, restore_expert, expert_config
        target,_=self.curation();apply_curation(target,self.db)
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db),"AUTH_MODE":"local_admin"}):
            draft=ExpertCreate(request_id=uuid4(),name="Reusable name",overview="Original",facets={"platform":["DRAM"]})
            original=create_expert(draft)
            deleted=delete_expert(original["id"],ExpertDelete(revision=expert_config(original["id"])["revision"]))
            restore_expert(original["id"],ExpertRestore(deleted_at=deleted["deleted_at"]))
            self.assertEqual(expert_config(original["id"])["draft"]["facets"]["platform"],["DRAM"])
            self.assertEqual(wikis(expert=original["id"],q="",limit=6,offset=0)["total"],1)
            deleted=delete_expert(original["id"],ExpertDelete(revision=expert_config(original["id"])["revision"]))
            replacement=create_expert(ExpertCreate(request_id=uuid4(),name="Reusable name",overview="Replacement"))
            self.assertNotEqual(original["id"],replacement["id"])
            with self.assertRaises(HTTPException) as conflict:
                restore_expert(original["id"],ExpertRestore(deleted_at=deleted["deleted_at"]))
            self.assertEqual(conflict.exception.status_code,409)
            with self.assertRaises(HTTPException):create_expert(draft)

    def test_expert_delete_requires_current_revision_and_admin(self):
        from fastapi import HTTPException
        from backend.expert_config import ExpertDelete, ExpertUpdate, ExpertRestore, expert_config, update_expert, delete_expert, restore_expert
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db),"AUTH_MODE":"local_admin"}):
            config=expert_config("dram")
            update_expert("dram",ExpertUpdate(**config["draft"],revision=config["revision"]))
            with self.assertRaises(HTTPException) as stale:delete_expert("dram",ExpertDelete(revision=config["revision"]))
            self.assertEqual(stale.exception.status_code,409)
            self.assertEqual(expert_config("dram")["draft"]["name"],"DRAM")
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db),"AUTH_MODE":"local_admin"}), patch("backend.auth.current_user",return_value={"employee_id":"test-user","role":"user","active":True}):
            for action in (lambda:delete_expert("dram",ExpertDelete(revision=config["revision"])),lambda:restore_expert("dram",ExpertRestore(deleted_at="untrusted"))):
                with self.assertRaises(HTTPException) as denied:action()
                self.assertEqual(denied.exception.status_code,403)

    def test_common_wiki_sources_are_independent_of_original_expert_configuration(self):
        target,_=self.curation();apply_curation(target,self.db)
        with connect(self.db) as db:
            paper_id=db.execute("SELECT paper_id FROM claims WHERE id='claim1'").fetchone()[0]
            db.execute("INSERT INTO expert_papers VALUES('modeling',?,'reviewed')",(paper_id,))
            db.execute("DELETE FROM expert_papers WHERE expert_id='dram'")
            sync_wiki_memberships(db);db.commit()
        with patch.dict(os.environ,{"EVIDENCE_DB_PATH":str(self.db)}):
            self.assertEqual(wikis(expert="dram",q="",limit=6,offset=0)["total"],0)
            self.assertEqual(wikis(expert="modeling",q="",limit=6,offset=0)["total"],1)
            self.assertEqual(wiki_detail("w1",limit=3,offset=0)["claims"][0]["id"],"claim1")

if __name__=="__main__": unittest.main()

