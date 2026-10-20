"""Local expert creation using cached metadata classifications and existing evidence."""
from __future__ import annotations
import hashlib
import json
import re
from uuid import UUID
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .pilot_db import ROOT, connect, now, sync_wiki_memberships
from .knowledge_inventory import reusable_common_wiki_ids

router = APIRouter(prefix="/api/pilot/experts", tags=["Experts"])
FACETS = json.loads((ROOT/"shared"/"expert_facets.json").read_text(encoding="utf-8"))
OPTIONS = {f["key"]: f["values"] for f in FACETS}
VERSION = "metadata-facets-v2"
PATTERNS = {
 "platform": {
  "DRAM":r"\bdrams?\b|\boctrams?\b", "NAND":r"nand", "NOR Flash":r"\bnor\b",
  "SRAM":r"\bsrams?\b", "HBM":r"\bhbm\b|high.bandwidth memory",
  "MRAM":r"\bmram\b|magnetoresistive", "RRAM":r"\brram\b|\breram\b|resistive.random",
  "PCM":r"\bpcm\b|phase.change", "Ferroelectric Memory":r"ferroelectric|fefet|fenand|\bfram\b|\bhzo\b|\bfesfet\b",
  "Selector Memory":r"selector|\bots\b", "Logic":r"\bgaa\b|cfet|\blogic\b|gate.all.around"
 },
 "focus": {
  "Reliability":r"reliab|retention|endurance|imprint|fatigue|variab|degradation|disturb|error rate",
  "Performance":r"performance|throughput|latency|bandwidth|speed|power|energy|delay",
  "Scaling":r"scal|density|miniaturi|\bcfet\b|cell.height|gate.pitch",
  "Mechanism":r"mechanism|physics|physical.model|polarization|trap|defect|migration|dynamics"
 },
 "layer": {
  "Device / Cell":r"device|transistor|capacitor|bit.cell|fefet|sram|dram",
  "Circuit / Array":r"circuit|array|sense.amplifier|ring.oscillator|spice|\btcam\b",
  "Process / Integration":r"process|fabricat|integrat|\bbeol\b|\bmeol\b|monolithic",
  "Packaging / Interconnect":r"packag|interconnect|\btsv\b|bonding",
  "Architecture / System":r"architecture|system|accelerator|workload|transformer",
  "Material / Physics":r"material|physics|physical|defect|ferroelectric|polarization|vacanc"
 },
 "application": {
  "CIM / PIM":r"\bcim\b|\bpim\b|computing.in.memory|compute.in.memory|in.memory.comput|in.storage.comput",
  "Neuromorphic":r"neuromorphic|synap|spiking",
  "CAM":r"\btcam\b|\bcam\b|content.addressable|in.memory.search"
 },
 "method": {
  "Experiment":r"experiment|measur|fabricat",
  "Simulation":r"simulat|\btcad\b|monte.carlo|spice|first.principles",
  "Review":r"\breview\b|\bperspective\b|we.survey"
 }
}

def can_manage_experts():
    from .auth import has_permission
    return has_permission('manage_experts')

def require_expert_manager():
    from .auth import require_permission
    require_permission('manage_experts')

class ExpertDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    overview: str = Field(min_length=1, max_length=1000)
    facets: dict[str, list[str]] = Field(default_factory=dict)
    included_ids: list[str] = Field(default_factory=list, max_length=500)
    excluded_ids: list[str] = Field(default_factory=list, max_length=500)
    enabled: bool = True

    @field_validator("name", "overview")
    @classmethod
    def trimmed(cls, value):
        value=value.strip()
        if not value: raise ValueError("이름과 개요를 입력하세요.")
        return value

    @field_validator("facets")
    @classmethod
    def valid_facets(cls, value):
        if any(key not in OPTIONS for key in value):
            raise ValueError("알 수 없는 분류 항목입니다.")
        for key, values in value.items():
            if any(v not in OPTIONS[key] for v in values):
                raise ValueError("알 수 없는 분류 값입니다.")
        return {key:list(dict.fromkeys(value.get(key, options))) for key,options in OPTIONS.items()}

class ExpertCreate(ExpertDraft):
    request_id: UUID

class ExpertUpdate(ExpertDraft):
    revision: str = Field(min_length=64, max_length=64)

class ExpertDelete(BaseModel):
    revision: str = Field(min_length=64, max_length=64)

class ExpertRestore(BaseModel):
    deleted_at: str = Field(min_length=1, max_length=64)


def stored_config(db, expert_id):
    expert=db.execute("SELECT * FROM expert_categories WHERE id=?",(expert_id,)).fetchone()
    if not expert or db.execute("SELECT 1 FROM expert_deletions WHERE expert_id=?",(expert_id,)).fetchone():
        raise HTTPException(404,"전문가를 찾을 수 없습니다.")
    policy=db.execute("SELECT policy_json FROM expert_policies WHERE expert_id=?",(expert_id,)).fetchone()
    if policy:
        draft=ExpertDraft.model_validate(json.loads(policy["policy_json"]))
    else:
        # Earlier curated experts have no saved facet choices. Preserve their exact
        # membership as explicit inclusions, without inventing original settings.
        members=[r[0] for r in db.execute("SELECT paper_id FROM expert_papers WHERE expert_id=? ORDER BY paper_id",(expert_id,))]
        draft=ExpertDraft(name=expert["name"],overview=expert["overview"],facets={"platform":[]},included_ids=members)
    data=draft.model_dump(mode="json")
    data.update(name=expert["name"],overview=expert["overview"])
    revision=hashlib.sha256(json.dumps({"id":expert_id,"draft":data,"legacy":not bool(policy)},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return {"id":expert_id,"draft":data,"revision":revision,"legacy":not bool(policy)}


def replace_memberships(db, expert_id, result):
    db.execute("DELETE FROM expert_papers WHERE expert_id=?",(expert_id,))
    for paper in result["admitted"]:
        db.execute("INSERT INTO expert_papers VALUES(?,?,?)",(expert_id,paper["id"],"expert-policy-v1"))

def classify_cached(db, paper):
    text=paper["title"]+"\n"+paper["abstract"]
    fingerprint=hashlib.sha256((VERSION+text).encode()).hexdigest()
    cached=db.execute("SELECT fingerprint,facets_json FROM paper_classifications WHERE paper_id=?", (paper["id"],)).fetchone()
    if cached and cached["fingerprint"]==fingerprint:
        return json.loads(cached["facets_json"])
    result={}
    for key, patterns in PATTERNS.items():
        source=paper["title"] if key=="platform" else text
        values=[name for name,pattern in patterns.items() if re.search(pattern,source,re.I)]
        # Platform terms mentioned only in the abstract may be a comparison, so leave them for review.
        result[key]=values
    db.execute("""INSERT INTO paper_classifications VALUES(?,?,?,?)
      ON CONFLICT(paper_id) DO UPDATE SET facets_json=excluded.facets_json,
      fingerprint=excluded.fingerprint,method=excluded.method""",
      (paper["id"],json.dumps(result,ensure_ascii=False),fingerprint,VERSION))
    return result

def preview_membership(db, draft, *, include_evidence_counts=True):
    criteria={k:draft.facets.get(k,list(v)) for k,v in OPTIONS.items()}
    restricted={k:v for k,v in criteria.items() if set(v)!=set(OPTIONS[k])}
    included,excluded=set(draft.included_ids),set(draft.excluded_ids)
    papers=[dict(p) for p in db.execute("SELECT id,title,abstract FROM papers ORDER BY title")]
    known={p["id"] for p in papers}
    if (included|excluded)-known:
        raise HTTPException(422,"존재하지 않는 논문이 선택되었습니다.")
    if included & excluded:
        raise HTTPException(422,"같은 논문을 편입과 제외에 동시에 지정할 수 없습니다.")
    admitted,review,removed=[],[],[]
    for p in papers:
        facets=classify_cached(db,p)
        row={"id":p["id"],"title":p["title"],"facets":facets}
        if p["id"] in excluded:
            removed.append(row);continue
        if p["id"] in included:
            row["reason"]="관리자 직접 편입";admitted.append(row);continue
        if any(not values for values in restricted.values()):
            continue
        matches=[None if not facets[key] else bool(set(values)&set(facets[key])) for key,values in restricted.items()]
        if False in matches:
            continue
        if None in matches:
            row["reason"]="선택한 분류 중 확인되지 않은 항목이 있습니다."
            review.append(row)
        else:
            row["reason"]="선택한 분류를 모두 만족합니다."
            admitted.append(row)
    ids={p["id"] for p in admitted}
    counts={"papers":len(ids),"review":len(review)}
    if include_evidence_counts:
        claims=[dict(c) for c in db.execute("SELECT id,paper_id FROM claims")]
        claim_ids={c["id"] for c in claims if c["paper_id"] in ids}
        measurements=len({r["measurement_id"] for r in db.execute("SELECT measurement_id,claim_id FROM claim_measurements") if r["claim_id"] in claim_ids})
        counts.update(claims=len(claim_ids),measurements=measurements,
                      wikis=len(reusable_common_wiki_ids(db,ids)))
    return {"admitted":admitted,"review":review,"excluded":removed,
            "counts":counts,
            "facets":criteria}

def refresh_expert_policies(db, *, paper_ids=None):
    for row in db.execute("SELECT * FROM expert_policies WHERE expert_id NOT IN (SELECT expert_id FROM expert_deletions)").fetchall():
        draft=ExpertDraft.model_validate(json.loads(row["policy_json"]))
        # Automatic refresh only needs membership, not a full knowledge inventory
        # for every expert. The public preview/create/update still return counts.
        result=preview_membership(db,draft,include_evidence_counts=False)
        replace_memberships(db,row["expert_id"],result)
    return sync_wiki_memberships(db,paper_ids=paper_ids)

@router.get("/options")
def expert_options():
    require_expert_manager()
    return {"facets":FACETS}

@router.post("/preview")
def expert_preview(draft:ExpertDraft):
    require_expert_manager()
    with connect() as db:
        result=preview_membership(db,draft)
        db.commit()
        return result

@router.post("",status_code=201)
def create_expert(draft:ExpertCreate):
    require_expert_manager()
    expert_id="custom-"+draft.request_id.hex
    policy=draft.model_dump(mode="json",exclude={"request_id"})
    serialized=json.dumps(policy,ensure_ascii=False,sort_keys=True)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM expert_deletions WHERE expert_id=?",(expert_id,)).fetchone():
            raise HTTPException(409,"삭제된 전문가의 생성 요청입니다. 새로 추가해 주세요.")
        existing=db.execute("SELECT policy_json FROM expert_policies WHERE expert_id=?",(expert_id,)).fetchone()
        if existing:
            if existing["policy_json"]!=serialized: raise HTTPException(409,"저장 요청이 변경되었습니다. 다시 추가해 주세요.")
            return {"id":expert_id,"name":draft.name,"status":"existing"}
        if any(r[0].casefold()==draft.name.casefold() for r in db.execute("SELECT name FROM expert_categories WHERE id NOT IN (SELECT expert_id FROM expert_deletions)")):
            raise HTTPException(409,"같은 이름의 전문가가 있습니다. 다른 이름을 입력해 주세요.")
        result=preview_membership(db,draft)
        db.execute("INSERT INTO expert_categories VALUES(?,?,?,?)",(expert_id,draft.name,draft.overview,""))
        db.execute("INSERT INTO expert_policies VALUES(?,?,?,?)",(expert_id,serialized,int(draft.enabled),now()))
        for p in result["admitted"]:
            db.execute("INSERT INTO expert_papers VALUES(?,?,?)",(expert_id,p["id"],"expert-policy-v1"))
        job_id=sync_wiki_memberships(db,expert_id=expert_id,paper_ids=[])
        from .processing import record_expert_creation
        record_expert_creation(db,expert_id,draft.name,job_id)
        db.commit()
        return {"id":expert_id,"name":draft.name,"status":"created","counts":result["counts"]}


@router.get("/{expert_id}")
def expert_config(expert_id:str):
    require_expert_manager()
    with connect() as db:
        return stored_config(db,expert_id)


@router.put("/{expert_id}")
def update_expert(expert_id:str, draft:ExpertUpdate):
    require_expert_manager()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current=stored_config(db,expert_id)
        if current["revision"]!=draft.revision:
            raise HTTPException(409,"다른 사용자가 설정을 변경했습니다. 창을 닫고 최신 설정을 다시 열어 주세요.")
        if any(r[0].casefold()==draft.name.casefold() for r in db.execute("SELECT name FROM expert_categories WHERE id<>? AND id NOT IN (SELECT expert_id FROM expert_deletions)",(expert_id,))):
            raise HTTPException(409,"같은 이름의 전문가가 있습니다. 다른 이름을 입력해 주세요.")
        result=preview_membership(db,draft)
        policy=draft.model_dump(mode="json",exclude={"revision"})
        serialized=json.dumps(policy,ensure_ascii=False,sort_keys=True)
        db.execute("UPDATE expert_categories SET name=?,overview=? WHERE id=?",(draft.name,draft.overview,expert_id))
        db.execute("""INSERT INTO expert_policies VALUES(?,?,?,?) ON CONFLICT(expert_id)
          DO UPDATE SET policy_json=excluded.policy_json,enabled=excluded.enabled""",
          (expert_id,serialized,int(draft.enabled),now()))
        replace_memberships(db,expert_id,result)
        sync_wiki_memberships(db,expert_id=expert_id,paper_ids=[])
        db.commit()
        return {"id":expert_id,"name":draft.name,"status":"updated","counts":result["counts"]}


@router.delete("/{expert_id}")
def delete_expert(expert_id:str, draft:ExpertDelete):
    require_expert_manager()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current=stored_config(db,expert_id)
        if current["revision"]!=draft.revision:
            raise HTTPException(409,"전문가 설정이 변경되었습니다. 창을 닫고 다시 확인해 주세요.")
        deleted_at=now()
        # Keep the original Wiki attribution and a reversible configuration snapshot.
        db.execute("INSERT INTO expert_deletions VALUES(?,?)",(expert_id,deleted_at))
        sync_wiki_memberships(db,expert_id=expert_id,paper_ids=[])
        db.commit()
        return {"id":expert_id,"name":current["draft"]["name"],"deleted_at":deleted_at}


@router.post("/{expert_id}/restore")
def restore_expert(expert_id:str, draft:ExpertRestore):
    require_expert_manager()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        deleted=db.execute("SELECT deleted_at FROM expert_deletions WHERE expert_id=?",(expert_id,)).fetchone()
        if not deleted or deleted["deleted_at"]!=draft.deleted_at:
            raise HTTPException(409,"삭제 상태가 변경되었습니다. 전문가 목록을 다시 확인해 주세요.")
        name=db.execute("SELECT name FROM expert_categories WHERE id=?",(expert_id,)).fetchone()[0]
        if any(r[0].casefold()==name.casefold() for r in db.execute("SELECT name FROM expert_categories WHERE id NOT IN (SELECT expert_id FROM expert_deletions)")):
            raise HTTPException(409,"같은 이름의 전문가가 있어 복원할 수 없습니다. 해당 전문가 이름을 먼저 변경해 주세요.")
        db.execute("DELETE FROM expert_deletions WHERE expert_id=?",(expert_id,))
        refresh_expert_policies(db,paper_ids=[])
        db.commit()
        return {"id":expert_id,"name":name}

