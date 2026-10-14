"""PDF ingestion, optional internal embeddings and queued Wiki maintenance."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
import time
import uuid
from pathlib import Path
from pypdf import PdfReader
from .pilot_db import ROOT, connect, initialize, now, is_database_busy
from .report_metadata import extract_title, extract_abstract, has_text_encoding_problem
from .expert_config import refresh_expert_policies
from .chunking import load_chunk_policy, reindex_document, prepare_index

PARSER_VERSION = "pypdf-6.10.0-text-v1"
TOPICS = {
 "DRAM": r"\bdram\b|dynamic random.access memory|\boctram\b",
 "NAND": r"\bnand\b|\bfenand\b",
 "NOR": r"\bnor\b",
 "SRAM": r"\bsram\b",
 "HBM": r"\bhbm\b|high.bandwidth memory",
 "MRAM": r"\bmram\b|magnetic tunnel junction",
 "RRAM": r"\b(?:rram|reram)\b|resistive switching",
 "FeRAM · FeFET": r"ferroelectric|\bfefet\b|\bferam\b",
 "CIM · PIM": r"comput(?:e|ing|ation).in.memory|processing.in.memory|\b[cp]im\b",
}
def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def classify(title, abstract):
    result = []
    for tag, pattern in TOPICS.items():
        if tag=="HBM" and re.search(r"human body model|\besd\b", title+" "+abstract, re.I) and not re.search(r"high.bandwidth memory", title+" "+abstract,re.I):
            continue
        title_hit = re.search(pattern,title,re.I)
        match = title_hit or re.search(pattern,abstract,re.I)
        if match:
            result.append((tag, ("title: " if title_hit else "abstract: ")+match.group(0)))
    return result

def ingest_file(path, db_path=None, stable_seconds=5, progress=lambda stage:None):
    path=Path(path).resolve()
    stat=path.stat()
    filename=path.name
    paper_id=None; wiki_job_id=None
    with connect(db_path) as db:
        def job(status, detail, digest=None):
            db.execute("INSERT INTO ingestion_jobs(filename,sha256,status,detail,created_at) VALUES(?,?,?,?,?)",(filename,digest,status,detail,now()))
            db.commit()
            return {"filename":filename,"status":status,"detail":detail,"paper_id":paper_id,"wiki_job_id":wiki_job_id}
        if time.time()-stat.st_mtime<stable_seconds:
            return job("deferred","파일 복사가 끝나기를 기다립니다.")
        digest=sha256_file(path)
        current_stat=path.stat()
        if (stat.st_size,stat.st_mtime_ns)!=(current_stat.st_size,current_stat.st_mtime_ns):
            return job("deferred","검사 중 파일이 변경되었습니다.",digest)
        def reuse_existing():
            nonlocal paper_id, wiki_job_id
            prior=db.execute("SELECT d.sha256 FROM document_sources s JOIN paper_documents d ON d.id=s.document_id WHERE s.path=?",(str(path),)).fetchone()
            if prior and prior["sha256"]!=digest:
                paper_id=None; wiki_job_id=None
                return job("needs_review","같은 경로의 내용이 변경되었습니다. 기존 문서와 근거를 유지합니다.",digest)
            document=db.execute("SELECT * FROM paper_documents WHERE sha256=?",(digest,)).fetchone()
            if document:
                paper_id=document['paper_id']
                from .wiki_engine import enqueue
                wiki_job_id=enqueue(db,'duplicate source linked')
                db.execute("INSERT INTO document_sources VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET last_seen_at=excluded.last_seen_at",(str(path),document["id"],filename,now(),now()))
                return job("unchanged","같은 SHA-256의 문서를 재사용했습니다.",digest)
            return None
        # Existing documents can be linked without repeating model work.
        if db.execute("SELECT 1 FROM document_sources WHERE path=? UNION SELECT 1 FROM paper_documents WHERE sha256=?", (str(path),digest)).fetchone():
            db.execute("BEGIN IMMEDIATE")
            reused=reuse_existing()
            if reused:return reused
            db.rollback()
        try:
            progress('논문 내용 읽는 중')
            reader=PdfReader(path)
            if reader.is_encrypted: raise ValueError("암호화된 PDF는 이 시범에서 처리하지 않습니다.")
            pages=[]
            for number,page in enumerate(reader.pages,1):
                text=(page.extract_text() or "").replace("\r\n","\n")
                text=text.encode("utf-8",errors="replace").decode("utf-8")
                state="needs_ocr" if len(re.sub(r"\s","",text))<80 else "needs_review" if has_text_encoding_problem(text) or text.count("\ufffd")>len(text)*0.02 else "ready"
                pages.append((number,text,state))
            after=path.stat()
            if (stat.st_size,stat.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or sha256_file(path)!=digest:
                return job("deferred","추출 도중 파일이 변경되어 결과를 저장하지 않았습니다.",digest)
            first=pages[0][1] if pages else ""
            title,source,_=extract_title(first,(reader.metadata or {}).get("/Title"),source_path=path)
            title=title or path.stem
            abstract=extract_abstract(first)
            paper_id="paper-"+digest[:20]
            document_id="doc-"+digest[:20]
            run_id="extract-"+uuid.uuid4().hex
            ready=sum(state=="ready" for _,_,state in pages)
            status="ready" if ready==len(pages) and pages else "partial" if ready else "needs_review"
            progress('문맥 분석·검색 준비 중')
            policy=load_chunk_policy()
            index_pages=[dict(id=run_id+"-p"+str(number),run_id=run_id,page_number=number,text=text,
                              method="pypdf",status=state,char_count=len(text)) for number,text,state in pages]
            prepared=prepare_index(db,run_id,title,index_pages,policy)
            after=path.stat()
            if (stat.st_size,stat.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or sha256_file(path)!=digest:
                paper_id=None
                return job("deferred","검색 준비 중 파일이 변경되어 결과를 저장하지 않았습니다.",digest)
            progress('DB 저장 중')
            db.execute("BEGIN IMMEDIATE")
            reused=reuse_existing()
            if reused:return reused
            db.execute("INSERT INTO papers VALUES(?,?,?,?,?,?)",(paper_id,title,abstract,source,"unreviewed",now()))
            db.execute("INSERT INTO paper_documents VALUES(?,?,?,?,?,?)",(document_id,paper_id,digest,stat.st_size,len(pages),run_id))
            db.execute("INSERT INTO document_sources VALUES(?,?,?,?,?)",(str(path),document_id,filename,now(),now()))
            db.execute("INSERT INTO extraction_runs VALUES(?,?,?,?,?)",(run_id,document_id,PARSER_VERSION,status,now()))
            for number,text,state in pages:
                page_id=run_id+"-p"+str(number)
                db.execute("INSERT INTO pages VALUES(?,?,?,?,?,?,?)",(page_id,run_id,number,text,"pypdf",state,len(text)))
            chunk_count=reindex_document(db,run_id,policy,prepared=prepared)["chunks"]
            for tag,basis in classify(title,abstract):
                db.execute("INSERT INTO paper_topics(paper_id,tag,basis) VALUES(?,?,?)",(paper_id,tag,basis))
                db.execute("INSERT OR IGNORE INTO expert_papers SELECT id,?,? FROM expert_categories WHERE topic_tag=? AND id NOT IN (SELECT expert_id FROM expert_deletions)",(paper_id,"title-abstract-rules-v1",tag))
            wiki_job_id=refresh_expert_policies(db)
            return job(status,f"{len(pages)} pages, {chunk_count} searchable chunks; {len(pages)-ready} pages need review",digest)
        except Exception as error:
            db.rollback()
            if is_database_busy(error):
                raise
            paper_id=None; wiki_job_id=None
            return job("failed","논문 처리에 실패했습니다. PDF 형식 또는 문서 분석 서비스 연결을 확인해 주세요.",digest)

def ingest(raw=ROOT/"raw", db_path=None, stable_seconds=5):
    initialize(db_path)
    results=[]
    for path in sorted(Path(raw).glob("*.pdf")):
        if path.is_symlink(): continue
        try: result=ingest_file(path,db_path,stable_seconds)
        except Exception as error: result={"filename":path.name,"status":"failed","detail":str(error)[:300]}
        results.append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    return results

if __name__=="__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser()
    parser.add_argument("--raw",type=Path,default=ROOT/"raw")
    parser.add_argument("--db",type=Path)
    parser.add_argument("--watch",type=int,default=0,help="Optional local polling interval in seconds")
    args=parser.parse_args()
    while True:
        ingest(args.raw,args.db)
        from .wiki_engine import run_jobs
        print(json.dumps(run_jobs(args.db),ensure_ascii=False),flush=True)
        if not args.watch: break
        time.sleep(max(10,args.watch))

