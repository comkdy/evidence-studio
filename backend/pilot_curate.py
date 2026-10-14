"""Import assistant-authored, source-checked evidence. Never extract new claims implicitly."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path
from .pilot_db import connect, initialize, now, sync_wiki_memberships
from .expert_config import refresh_expert_policies
from .scientific_values import validate_scientific_value
from .chunking import load_chunk_policy, reindex_document, prepare_document_index, validate_prepared_input

def locate_quote(text, quote):
    # Match only whitespace-normalized literal text; never fuzzy-match a scientific value.
    chars=[];positions=[]
    for token in re.finditer(r"\S+",text):
        if chars: chars.append(" ");positions.append(token.start())
        chars.extend(token.group());positions.extend(range(token.start(),token.end()))
    normalized="".join(chars)
    wanted=" ".join(quote.split())
    start=normalized.find(wanted)
    if start<0 or normalized.find(wanted,start+1)>=0:
        raise ValueError("Source quotation is missing or ambiguous")
    return positions[start], positions[start+len(wanted)-1]+1

def apply_curation(path,db_path=None):
    initialize(db_path)
    raw=Path(path).read_bytes()
    payload=json.loads(raw)
    digest=hashlib.sha256(raw).hexdigest()
    run_id="curation-"+digest[:20]
    with connect(db_path) as db:
        if db.execute("SELECT 1 FROM curation_runs WHERE payload_sha256=?",(digest,)).fetchone():
            return {"status":"unchanged","run_id":run_id}
        prepared={}
        policy=load_chunk_policy()
        for paper in payload["papers"]:
            if not paper.get("corrected_title"):continue
            document=db.execute("SELECT d.current_run_id,p.title FROM paper_documents d JOIN papers p ON p.id=d.paper_id WHERE d.sha256=?", (paper["sha256"],)).fetchone()
            if document and document["title"]!=paper["corrected_title"]:
                prepared[paper["sha256"]]=prepare_document_index(db,document["current_run_id"],policy,title=paper["corrected_title"])
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM curation_runs WHERE payload_sha256=?",(digest,)).fetchone():
            return {"status":"unchanged","run_id":run_id}
        for plan in prepared.values():
            validate_prepared_input(db,plan)
        # The pilot adds immutable records. Changed IDs require an explicit new revision.
        db.execute("INSERT INTO curation_runs VALUES(?,?,?,?,?)",(run_id,"Codex","assistant-read-and-structure; source quote matched; human review pending",digest,now()))
        for paper in payload["papers"]:
            document=db.execute("SELECT * FROM paper_documents WHERE sha256=?",(paper["sha256"],)).fetchone()
            if not document: raise ValueError("Curated PDF hash is not in this DB: "+paper["filename"])
            paper_id=document["paper_id"]
            def span(source):
                page=db.execute("SELECT * FROM pages WHERE run_id=? AND page_number=?",(document["current_run_id"],source["page"])).fetchone()
                if not page: raise ValueError("Missing source page")
                start,end=locate_quote(page["text"],source["quote"])
                key=hashlib.sha256((page["id"]+":"+str(start)+":"+str(end)).encode()).hexdigest()[:24]
                id="span-"+key
                db.execute("INSERT OR IGNORE INTO evidence_spans VALUES(?,?,?,?,?,?,?)",(id,paper_id,page["id"],start,end,page["text"][start:end],source.get("locator","본문")))
                return id
            if paper.get("corrected_title"):
                sid=span(paper["title_source_quote"])
                old_title=db.execute("SELECT title FROM papers WHERE id=?",(paper_id,)).fetchone()[0]
                if old_title!=paper["corrected_title"]:
                    db.execute("INSERT INTO paper_metadata_changes(paper_id,curation_id,field,previous_value,corrected_value,source_span_id) VALUES(?,?,?,?,?,?)",(paper_id,run_id,"title",old_title,paper["corrected_title"],sid))
                    db.execute("UPDATE papers SET title=?,title_source='assistant-source-reviewed',metadata_status='human_review_pending' WHERE id=?",(paper["corrected_title"],paper_id))
                    plan=prepared.get(paper["sha256"])
                    if not plan:raise ValueError("Title changed during preparation; retry curation")
                    reindex_document(db,document["current_run_id"],policy,prepared=plan)
            for claim in paper["claims"]:
                db.execute("INSERT INTO claims(id,paper_id,curation_id,summary,kind,scope) VALUES(?,?,?,?,?,?)",(claim["id"],paper_id,run_id,claim["summary"],claim["kind"],claim["scope"]))
                for source in claim["sources"]:
                    db.execute("INSERT INTO claim_evidence VALUES(?,?,?)",(claim["id"],span(source),paper_id))
                for measure in claim.get("measurements",[]):
                    validate_scientific_value(measure["raw_value"],measure.get("numeric_value"))
                    db.execute("INSERT INTO measurements VALUES(?,?,?,?,?,?,?,?,?,?)",(measure["id"],paper_id,run_id,measure["label"],measure["raw_value"],measure["operator"],measure.get("numeric_value"),measure["unit"],measure["sample"],measure["kind"]))
                    db.execute("INSERT INTO claim_measurements VALUES(?,?,?,?)",(claim["id"],measure["id"],paper_id,"reported-result"))
                    for source in measure.get("sources",claim["sources"]):
                        db.execute("INSERT INTO measurement_evidence VALUES(?,?,?)",(measure["id"],span(source),paper_id))
                    for i,condition in enumerate(measure.get("conditions",[])):
                        sid=span(condition["source"]) if condition.get("raw_value") is not None else None
                        db.execute("INSERT INTO condition_values VALUES(?,?,?,?,?,?)",(measure["id"]+"-cond-"+str(i),measure["id"],paper_id,condition["name"],condition.get("raw_value"),sid))
            for expert in paper.get("experts",[]):
                db.execute("INSERT OR IGNORE INTO expert_papers VALUES(?,?,?)",(expert,paper_id,"assistant-reviewed-paper-scope"))
        for wiki in payload.get("wikis",[]):
            for claim_id in wiki["claim_ids"]:
                found=db.execute("SELECT 1 FROM claims c JOIN expert_papers ep ON ep.paper_id=c.paper_id WHERE c.id=? AND ep.expert_id=?",(claim_id,wiki["expert_id"])).fetchone()
                if not found: raise ValueError("Wiki evidence outside expert scope")
            db.execute("INSERT INTO knowledge_pages VALUES(?,?,?,?,?,?,?)",(wiki["id"],wiki["expert_id"],run_id,wiki["title"],wiki["body"],wiki["limitation"],now()))
            for claim_id in wiki["claim_ids"]:
                db.execute("INSERT INTO knowledge_links VALUES(?,?)",(wiki["id"],claim_id))
        refresh_expert_policies(db)
        db.commit()
        return {"status":"created","run_id":run_id,"claims":sum(len(p["claims"]) for p in payload["papers"])}

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("path",type=Path)
    parser.add_argument("--db",type=Path)
    args=parser.parse_args()
    print(json.dumps(apply_curation(args.path,args.db)))
    from .wiki_engine import run_jobs
    print(json.dumps(run_jobs(args.db),ensure_ascii=False))

