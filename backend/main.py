"""Local pilot API plus the separately named fictional prototype API.

Run from the project root:
  python -m uvicorn backend.main:app --host 127.0.0.1 --port 8011
"""
from pathlib import Path
from contextlib import asynccontextmanager
import json
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .pilot_api import router as pilot_router
from .expert_config import router as expert_router, refresh_expert_policies
from .pilot_db import initialize, connect, sync_wiki_memberships
from fastapi.staticfiles import StaticFiles
from .ai_settings import load_ai_settings
from .wiki_engine import start_worker
from .wiki_api import router as wiki_router
from .auth import router as auth_router, authentication_middleware
from .user_api import router as user_router
from .processing import router as processing_router
from .page_review import router as page_review_router
from .terminology_api import router as terminology_router

ROOT = Path(__file__).resolve().parents[1]
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.ai_settings = load_ai_settings()
    initialize()
    with connect() as db:
        refresh_expert_policies(db)
        db.commit()
    stop, workers = start_worker()
    try:
        yield
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=2)


app = FastAPI(title="Evidence Studio", version="0.3.0", description="Evidence-backed Markdown Wiki and research library.", lifespan=lifespan)
app.include_router(pilot_router)
app.include_router(expert_router)
app.include_router(wiki_router)
app.include_router(auth_router)
app.include_router(user_router)
app.include_router(processing_router)
app.include_router(page_review_router)
app.include_router(terminology_router)
app.middleware('http')(authentication_middleware)


class Paper(BaseModel):
    id: str
    title: str
    authors: str
    year: int
    journal: str
    topic: str
    pages: int
    chunks: int
    filename: str
    abstract: str
    evidence: str


class LibraryResponse(BaseModel):
    mode: Literal["prototype"] = "prototype"
    papers: list[Paper]


def example_papers() -> list[Paper]:
    return [Paper.model_validate(row) for row in json.loads((ROOT / "shared" / "papers.json").read_text(encoding="utf-8"))]


@app.get("/api/health")
def health():
    with connect() as db:
        db.execute("SELECT version FROM schema_meta").fetchone()
    from .llm_client import CompanyLLM
    return {"status": "ok", "mode": "local-pilot", "ingestion_enabled": True, "llm_enabled": CompanyLLM().configured, "database_connected": True, "prototype_api": "/api/papers", "pilot_api": "/api/pilot"}


@app.get("/api/papers", response_model=LibraryResponse)
def list_papers():
    return LibraryResponse(papers=example_papers())


@app.get("/api/papers/{paper_id}", response_model=Paper)
def get_paper(paper_id: str):
    for paper in example_papers():
        if paper.id == paper_id:
            return paper
    raise HTTPException(status_code=404, detail="Example paper not found")


@app.get("/api/ingestion/policy")
def ingestion_policy():
    return {
        "mode": "prototype", "enabled": False, "raw_path": "./raw",
        "steps": ["stabilizing", "hashing", "extracting", "validating", "indexing"],
        "review_states": ["ocr_required", "human_review_required", "failed_permanent"],
        "reference": "Report AI design review; not a connection to its running application",
    }


# The pilot and the original prototype share assets, but never share datasets.
if (ROOT / "dist").is_dir():
    app.mount("/", StaticFiles(directory=ROOT / "dist", html=True), name="ui")
