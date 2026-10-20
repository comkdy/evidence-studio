from __future__ import annotations
import re
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from .pilot_db import connect, db_path, SCHEMA
from .pilot_ingest import sha256_file
from .expert_config import can_manage_experts, classify_cached, OPTIONS
from .abstract_summaries import attach_summaries

router=APIRouter(prefix="/api/pilot",tags=["Local pilot"])
TABLES={
 "paper_abstract_summaries":"초록 기반 한국어 요약·출처 버전",
 "chunk_embedding_cache":"문맥 경계 판정용 embedding 캐시", "search_semantic_audits":"의미 경계·거리·길이 제한 분할 기록",
 "papers":"논문 서지정보", "paper_documents":"해시로 구분한 PDF 문서", "document_sources":"발견한 파일 경로",
 "extraction_runs":"추출 실행과 버전", "pages":"페이지별 원문·추출 상태", "search_chunks":"문맥과 출처에 연결된 검색 구간",
 "search_contexts":"검색 근거의 상위 문맥", "search_chunk_details":"검색 구간의 문맥·section",
 "search_chunk_spans":"여러 페이지의 정확한 원문 위치", "search_index_state":"분할 정책·tokenizer·색인 버전",
 "paper_topics":"제목·초록에서 판정한 분류", "expert_categories":"전문가", "expert_papers":"전문가 담당 논문",
 "curation_runs":"Codex 구조화 작업 출처", "evidence_spans":"원문 위치와 정확한 구절",
 "claims":"확인한 주장·적용 범위", "claim_evidence":"주장과 원문 연결", "measurements":"수치·단위·연산자",
 "claim_measurements":"주장에 직접 연결된 수치", "measurement_evidence":"수치와 원문 연결",
 "condition_values":"수치별 조건·미기재", "knowledge_pages":"근거를 재사용한 Wiki", "knowledge_links":"Wiki가 사용하는 주장",
 "ingestion_jobs":"파일별 적재 기록",
 "expert_knowledge_pages":"전문가별 공통 Wiki 재사용 연결",
 "paper_classifications":"공통 논문 분류 캐시", "expert_policies":"전문가 편입 기준",
 "expert_deletions":"전문가 삭제 상태",
 "paper_metadata_changes":"제목 교정과 원문·이전 값",
 "wiki_files":"Markdown Wiki 파일·근거·색인 버전", "wiki_revisions":"Wiki의 불변 변경 이력",
 "wiki_jobs":"Wiki 갱신 작업", "wiki_lint_runs":"정기 구조 검사·LLM 내용 검토",
}
def tokens_for(query):
    from .terminology import query_rules
    query=query_rules(query)['normalized']
    words=re.findall(r"[A-Za-z0-9]+",query.lower())
    stop={"what","is","the","a","an","of","in","at","for","and","with","how","does","to"}
    return list(dict.fromkeys(word for word in words if word not in stop))[:12]

def rows(cursor): return [dict(row) for row in cursor.fetchall()]

def expert_scope(expert, paper_alias="p"):
    ids=list(dict.fromkeys(x.strip() for x in expert.split(",") if x.strip()))[:20]
    if not ids: return "1",()
    return paper_alias+".id IN (SELECT paper_id FROM expert_papers WHERE expert_id IN ("+",".join("?" for _ in ids)+") AND expert_id NOT IN (SELECT expert_id FROM expert_policies WHERE enabled=0) AND expert_id NOT IN (SELECT expert_id FROM expert_deletions))",tuple(ids)

def claim_detail(db,row):
    item=dict(row)
    item["sources"]=rows(db.execute("SELECT e.*,p.page_number FROM claim_evidence ce JOIN evidence_spans e ON e.id=ce.span_id JOIN pages p ON p.id=e.page_id WHERE ce.claim_id=?",(item["id"],)))
    item["measurements"]=rows(db.execute("SELECT m.* FROM claim_measurements cm JOIN measurements m ON m.id=cm.measurement_id WHERE cm.claim_id=?",(item["id"],)))
    for m in item["measurements"]:
        m["conditions"]=rows(db.execute("SELECT * FROM condition_values WHERE measurement_id=?",(m["id"],)))
        m["sources"]=rows(db.execute("SELECT e.*,p.page_number FROM measurement_evidence me JOIN evidence_spans e ON e.id=me.span_id JOIN pages p ON p.id=e.page_id WHERE me.measurement_id=?",(m["id"],)))
        for condition in m["conditions"]:
            condition["source"]=next(iter(rows(db.execute("SELECT e.*,p.page_number FROM evidence_spans e JOIN pages p ON p.id=e.page_id WHERE e.id=?",(condition["span_id"],)))),None) if condition["span_id"] else None
    item["experts"]=rows(db.execute("SELECT e.id,e.name FROM expert_categories e JOIN expert_papers ep ON ep.expert_id=e.id WHERE ep.paper_id=? AND e.id NOT IN (SELECT expert_id FROM expert_deletions) ORDER BY e.name",(item["paper_id"],)))
    return item

@router.get("/summary")
def summary():
    with connect() as db:
        counts={name:db.execute('SELECT count(*) FROM "'+name+'"').fetchone()[0] for name in TABLES}
        states=rows(db.execute("SELECT pg.status,count(*) AS count FROM pages pg JOIN paper_documents d ON d.current_run_id=pg.run_id GROUP BY pg.status"))
        experts=rows(db.execute("""SELECT e.*,count(ep.paper_id) AS paper_count,
        coalesce((SELECT enabled FROM expert_policies WHERE expert_id=e.id),1) AS enabled,
        (SELECT count(*) FROM claims c JOIN expert_papers x ON x.paper_id=c.paper_id WHERE x.expert_id=e.id) AS claim_count,
        (SELECT count(*) FROM expert_knowledge_pages ek WHERE ek.expert_id=e.id) AS wiki_count,
        (SELECT max(k.created_at) FROM expert_knowledge_pages ek JOIN knowledge_pages k ON k.id=ek.page_id WHERE ek.expert_id=e.id) AS updated_at
        FROM expert_categories e LEFT JOIN expert_papers ep ON ep.expert_id=e.id WHERE e.id NOT IN (SELECT expert_id FROM expert_deletions) GROUP BY e.id ORDER BY e.name"""))
        counts["expert_categories"]=len(experts)
        if counts['wiki_files']:
            from .wiki_api import load_catalogue, in_scope
            from .wiki_engine import CONTENT_KINDS
            _,wiki_state,catalogue=load_catalogue()
            for e in experts:
                own=[r for r in catalogue.values() if r['kind'] in CONTENT_KINDS and in_scope(r,e['id'],wiki_state)]
                e['wiki_count']=len(own)
                e['updated_at']=max((r['updated_at'] for r in own),default=None)
        latest=db.execute("SELECT max(created_at) FROM extraction_runs").fetchone()[0]
        reviewed=db.execute("SELECT count(DISTINCT paper_id) FROM claims").fetchone()[0]
        from .ai_settings import load_ai_settings
        settings=load_ai_settings()
        def configured(service):
            return bool(service.base_url and service.model and service.api_key.get_secret_value())
        return {"mode":"local-pilot","database":"SQLite + FTS5","counts":counts,"page_states":states,"experts":experts,"updated_at":latest,"curated_papers":reviewed,"can_manage_experts":can_manage_experts(),"llm_enabled":configured(settings.llm),"embedding_enabled":configured(settings.embedding),"curation":"Codex 작성 · 원문 문자열 대조 · 사람 검토 대기"}

@router.get("/schema")
def schema():
    with connect() as db:
        tables=[]
        for name,label in TABLES.items():
            tables.append({"name":name,"label":label,"count":db.execute('SELECT count(*) FROM "'+name+'"').fetchone()[0],"columns":rows(db.execute('PRAGMA table_info("'+name+'")')),"foreign_keys":rows(db.execute('PRAGMA foreign_key_list("'+name+'")'))})
        return {"tables":tables,"sql":SCHEMA.read_text(encoding="utf-8"),"database_file":db_path().name}

@router.get("/papers")
def papers(q:str="",expert:str="",limit:int=Query(10,ge=1,le=50),offset:int=Query(0,ge=0),facets:str=""):
    from .terminology import query_rules
    normalized=query_rules(q)['normalized'] if q else ''
    try:
        selected=json.loads(facets) if facets else {}
        if not isinstance(selected,dict): raise ValueError()
        if any(k not in OPTIONS or not isinstance(v,list) or any(x not in OPTIONS[k]+["__unclassified__"] for x in v) for k,v in selected.items()):
            raise ValueError()
    except (ValueError,TypeError):
        raise HTTPException(422,"카테고리 선택이 올바르지 않습니다.")
    with connect() as db:
        where=" WHERE (?='' OR p.title LIKE ? OR p.title LIKE ? OR p.id IN (SELECT paper_id FROM paper_topics WHERE tag LIKE ? OR tag LIKE ?)) AND (?='' OR p.id IN (SELECT paper_id FROM expert_papers WHERE expert_id=? AND expert_id NOT IN (SELECT expert_id FROM expert_deletions)))"
        params=(q,"%"+q+"%","%"+normalized+"%","%"+q+"%","%"+normalized+"%",expert,expert)
        total=db.execute("SELECT count(*) FROM papers p"+where,params).fetchone()[0]
        result=rows(db.execute("""SELECT p.*, d.id AS document_id,d.page_count,r.status,
          (SELECT filename FROM document_sources WHERE document_id=d.id ORDER BY filename LIMIT 1) AS filename,
          (SELECT count(*) FROM claims WHERE paper_id=p.id) AS claim_count,
          (SELECT count(*) FROM search_chunks c JOIN pages pg ON c.page_id=pg.id WHERE pg.run_id=d.current_run_id) AS chunk_count
          FROM papers p JOIN paper_documents d ON d.paper_id=p.id JOIN extraction_runs r ON r.id=d.current_run_id"""+where+" ORDER BY claim_count DESC,p.title",params))
        for p in result: p["facets"]=classify_cached(db,p)
        def matches(p,ignore=None):
            return all(not values or key==ignore or bool(set(values)&set(p["facets"][key] or ["__unclassified__"])) for key,values in selected.items())
        facet_counts={key:{value:sum(matches(p,key) and value in (p["facets"][key] or ["__unclassified__"]) for p in result) for value in values+["__unclassified__"]} for key,values in OPTIONS.items()}
        result=[p for p in result if matches(p)]
        total=len(result)
        result=result[offset:offset+limit]
        attach_summaries(db,result)
        for p in result:
            p["topics"]=rows(db.execute("SELECT tag,basis,method FROM paper_topics WHERE paper_id=?",(p["id"],)))
        db.commit()
        return {"total":total,"papers":result,"facet_counts":facet_counts}

@router.get("/papers/{paper_id}")
def paper(paper_id:str):
    with connect() as db:
        record=db.execute("SELECT p.*,d.id AS document_id,d.sha256,d.page_count,d.current_run_id FROM papers p JOIN paper_documents d ON p.id=d.paper_id WHERE p.id=?",(paper_id,)).fetchone()
        if not record: raise HTTPException(404,"Paper not found")
        result=dict(record)
        attach_summaries(db,[result])
        result["pages"]=rows(db.execute("SELECT id,page_number,status,char_count FROM pages WHERE run_id=? ORDER BY page_number",(record["current_run_id"],)))
        result["claims"]=[claim_detail(db,r) for r in db.execute("SELECT * FROM claims WHERE paper_id=? ORDER BY id",(paper_id,)).fetchall()]
        from .knowledge_build import coverage_status
        coverage=coverage_status(db,paper_ids=[paper_id])['papers'][0]
        experts=rows(db.execute('''SELECT e.id,e.name,coalesce(p.enabled,1) AS enabled
            FROM expert_categories e JOIN expert_papers ep ON ep.expert_id=e.id
            LEFT JOIN expert_policies p ON p.expert_id=e.id
            WHERE ep.paper_id=? AND e.id NOT IN (SELECT expert_id FROM expert_deletions)
            ORDER BY e.name''',(paper_id,)))
    # Structured claims and reviewed source coverage are independent: a paper
    # can already support reviewed Wiki material before curated claims exist.
    from .wiki_api import load_catalogue,read_page,item
    from .wiki_store import WikiError
    store,state,catalogue=load_catalogue()
    common=[]
    for row in sorted(catalogue.values(),key=lambda row:(row['kind']!='source',row['title'],row['id'])):
        if row['scope']!='common' or row['kind'] not in ('source','topic') or paper_id not in row['refs']['paper_ids']:
            continue
        try:
            common.append(item(read_page(row,store,catalogue),state))
        except WikiError:
            # A changed file or dependency is not a usable knowledge link.
            continue
    result['knowledge']=dict(coverage=coverage,common_wikis=common,
                             experts=[dict(expert,enabled=bool(expert['enabled'])) for expert in experts])
    return result

@router.get("/pages/{page_id}")
def page(page_id:str):
    with connect() as db:
        row=db.execute("SELECT * FROM pages WHERE id=?",(page_id,)).fetchone()
        if not row: raise HTTPException(404,"Page not found")
        return dict(row)

@router.get("/documents/{document_id}/pdf")
def pdf(document_id:str):
    with connect() as db:
        doc=db.execute("SELECT * FROM paper_documents WHERE id=?",(document_id,)).fetchone()
        if not doc: raise HTTPException(404,"Document not found")
        sources=db.execute("SELECT path FROM document_sources WHERE document_id=?",(document_id,)).fetchall()
        for source in sources:
            path=Path(source["path"])
            if path.is_file() and sha256_file(path)==doc["sha256"]:
                return FileResponse(path,media_type="application/pdf",filename=path.name,content_disposition_type="inline")
        raise HTTPException(409,"원본 파일이 이동되었거나 변경되었습니다. 저장된 근거와 일치하는 PDF가 필요합니다.")

@router.get("/search")
def search(q:str=Query(...,max_length=300),expert:str="",limit:int=Query(8,ge=1,le=30)):
    from .terminology import conjunctive_groups, equivalent
    terms=tokens_for(q)
    groups=conjunctive_groups(q)
    if not groups: return {"terms":[],"hits":[],"claims":[],"total_claims":0,"note":"영어 전문 용어 또는 지원하는 한국어 별칭을 입력하세요."}
    expression=' AND '.join('('+' OR '.join('"'+term.replace('"','""')+'"' for term in group)+')' for group in groups)
    with connect() as db:
        selected=[]
        scope,scope_args=expert_scope(expert)
        # Restrict raw passages and structured claims to the same selected experts.
        hits=rows(db.execute("""SELECT c.id,c.text,pg.id AS page_id,pg.page_number,p.id AS paper_id,p.title,d.id AS document_id,bm25(chunk_fts,0,2,1) AS score
         FROM chunk_fts JOIN search_chunks c ON c.id=chunk_fts.chunk_id JOIN pages pg ON pg.id=c.page_id
         JOIN paper_documents d ON d.current_run_id=pg.run_id JOIN papers p ON p.id=d.paper_id
         WHERE chunk_fts MATCH ? AND """+scope+" ORDER BY score LIMIT ?",(expression,*scope_args,limit)))
        for hit in hits:
            spans=rows(db.execute("SELECT * FROM search_chunk_spans WHERE chunk_id=? ORDER BY ordinal",(hit["id"],)))
            hit["source_spans"]=spans
            hit["page_end"]=max((s["page_number"] for s in spans),default=hit["page_number"])
            context=db.execute("""SELECT p.text,p.section,p.source_spans_json FROM search_chunk_details c
                JOIN search_contexts p ON p.id=c.parent_id WHERE c.chunk_id=?""",(hit["id"],)).fetchone()
            if context:
                hit["parent_context"]={"text":context["text"],"section":context["section"],
                    "pages":sorted({s["page_number"] for s in json.loads(context["source_spans_json"])})}
        for row in db.execute("""SELECT c.*,p.title,d.id AS document_id FROM claims c JOIN papers p ON p.id=c.paper_id
          JOIN paper_documents d ON d.paper_id=p.id WHERE """+scope+" ORDER BY p.title,c.id",scope_args).fetchall():
            item=claim_detail(db,row)
            haystack=equivalent(" ".join([item["title"],item["summary"],item["scope"],*[s["quote"] for s in item["sources"]]]))
            if all(any(re.search(r"(?<![a-z0-9])"+re.escape(term)+r"(?![a-z0-9])",haystack) for term in group) for group in groups):
                selected.append(item)
        return {"terms":terms,"hits":hits,"claims":selected[:limit],"total_claims":len(selected),"note":"제목·원문·정리된 주장에 키워드가 모두 포함된 근거입니다. 종합 답변과 심층 분석은 연구 질문에서 요청할 수 있습니다."}

@router.get("/wikis")
def wikis(expert:str="",q:str="",limit:int=Query(6,ge=1,le=20),offset:int=Query(0,ge=0)):
    with connect() as db:
        from .wiki_api import migrated, list_wikis
        if migrated(db):
            return list_wikis(expert,q,limit,offset)
        where=" WHERE (?='' OR k.id IN (SELECT page_id FROM expert_knowledge_pages WHERE expert_id=? AND expert_id NOT IN (SELECT expert_id FROM expert_deletions))) AND (?='' OR k.title LIKE ? OR k.body LIKE ?)"
        params=(expert,expert,q,"%"+q+"%","%"+q+"%")
        total=db.execute("SELECT count(*) FROM knowledge_pages k"+where,params).fetchone()[0]
        result=rows(db.execute("""SELECT k.*,e.name AS expert_name,
         (SELECT count(*) FROM knowledge_links l WHERE l.page_id=k.id) AS claim_count,
         (SELECT count(DISTINCT c.paper_id) FROM knowledge_links l JOIN claims c ON c.id=l.claim_id WHERE l.page_id=k.id) AS paper_count
         FROM knowledge_pages k JOIN expert_categories e ON e.id=k.expert_id"""+where+" ORDER BY k.created_at DESC,k.id LIMIT ? OFFSET ?",(*params,limit,offset)))
        return {"wikis":result,"total":total}

@router.get("/wikis/{wiki_id}")
def wiki_detail(wiki_id:str,limit:int=Query(3,ge=1,le=10),offset:int=Query(0,ge=0)):
    with connect() as db:
        from .wiki_api import migrated, detail
        if migrated(db):
            return detail(wiki_id,limit,offset)
        record=db.execute("SELECT k.*,e.name AS expert_name FROM knowledge_pages k JOIN expert_categories e ON e.id=k.expert_id WHERE k.id=?",(wiki_id,)).fetchone()
        if not record: raise HTTPException(404,"Wiki not found")
        wiki=dict(record)
        # Common Wiki evidence belongs to its canonical claim links, not its original author.
        # Expert-specific visibility is enforced by expert_knowledge_pages in wikis().
        base=""" FROM knowledge_links l JOIN claims c ON c.id=l.claim_id JOIN papers p ON p.id=c.paper_id
         JOIN paper_documents d ON d.paper_id=p.id WHERE l.page_id=?"""
        params=(wiki_id,)
        wiki["total_claims"]=db.execute("SELECT count(*)"+base,params).fetchone()[0]
        wiki["claims"]=[claim_detail(db,r) for r in db.execute("SELECT c.*,p.title,d.id AS document_id"+base+" ORDER BY c.id LIMIT ? OFFSET ?",(*params,limit,offset)).fetchall()]
        return wiki

@router.get("/recommend")
def recommend(q:str=Query(...,max_length=300)):
    from .wiki_api import migrated, recommendations
    with connect() as db:
        if migrated(db):
            return recommendations(q)
    terms=tokens_for(q)
    if not terms: return {"experts":[],"terms":[]}
    expression=" OR ".join('"'+t+'"' for t in terms)
    with connect() as db:
        result=rows(db.execute("""SELECT e.id,e.name,e.overview,count(DISTINCT p.id) AS matching_papers
        FROM chunk_fts JOIN search_chunks c ON c.id=chunk_fts.chunk_id JOIN pages pg ON pg.id=c.page_id
        JOIN paper_documents d ON d.current_run_id=pg.run_id JOIN papers p ON p.id=d.paper_id
        JOIN expert_papers ep ON ep.paper_id=p.id JOIN expert_categories e ON e.id=ep.expert_id
        WHERE chunk_fts MATCH ? AND e.id NOT IN (SELECT expert_id FROM expert_policies WHERE enabled=0)
        AND e.id NOT IN (SELECT expert_id FROM expert_deletions)
        GROUP BY e.id ORDER BY matching_papers DESC,e.name LIMIT 4""",(expression,)))
        return {"experts":result,"terms":terms,"method":"키워드와 담당 논문 일치 기반 추천 · LLM 미연결"}

@router.get("/status")
def corpus_status():
    with connect() as db:
        issues=rows(db.execute("""SELECT p.id AS paper_id,p.title,d.id AS document_id,pg.id AS page_id,pg.page_number,pg.status,pg.char_count,pg.text AS review_text,
        (SELECT count(*) FROM page_review_requests r WHERE r.page_id=pg.id AND r.state='pending') AS pending_requests
        FROM pages pg JOIN paper_documents d ON d.current_run_id=pg.run_id JOIN papers p ON p.id=d.paper_id
        WHERE pg.status NOT IN ('ready','excluded') ORDER BY p.title,pg.page_number"""))
        for issue in issues:issue['char_count']=len(re.sub(r'\s','',issue.pop('review_text')))
        reviewed=rows(db.execute("""SELECT p.id AS paper_id,p.title,d.id AS document_id,pg.id AS page_id,pg.page_number,pg.status,pg.char_count,
        (SELECT count(*) FROM page_review_requests r WHERE r.page_id=pg.id AND r.state='pending') AS pending_requests
        FROM pages pg JOIN paper_documents d ON d.current_run_id=pg.run_id JOIN papers p ON p.id=d.paper_id
        WHERE pg.status IN ('ready','excluded') AND EXISTS(SELECT 1 FROM page_review_requests r WHERE r.page_id=pg.id)
        ORDER BY (SELECT max(created_at) FROM page_review_requests r WHERE r.page_id=pg.id) DESC LIMIT 20"""))
        changes=rows(db.execute("""SELECT r.id,r.created_at,r.producer,r.method,
        (SELECT count(*) FROM claims WHERE curation_id=r.id) AS claims,
        (SELECT count(*) FROM measurements WHERE curation_id=r.id) AS measurements,
        (SELECT count(*) FROM knowledge_pages WHERE curation_id=r.id) AS wiki_pages,
        (SELECT count(DISTINCT paper_id) FROM claims WHERE curation_id=r.id) AS papers
        FROM curation_runs r ORDER BY r.created_at DESC LIMIT 10"""))
        unresolved=rows(db.execute("SELECT id,title FROM papers WHERE title_source='unresolved'"))
        return {"issues":issues,"reviewed_pages":reviewed,"changes":changes,"unresolved_titles":unresolved}

