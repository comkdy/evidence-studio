"""SQLite schema initialization and shared research data connections."""
from __future__ import annotations
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from .identity_settings import bootstrap_admin_id

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "evidence-studio.sqlite3"
SCHEMA = Path(__file__).with_name("pilot_schema.sql")

def now():
    return datetime.now(timezone.utc).isoformat()

def db_path():
    return Path(os.environ.get("EVIDENCE_DB_PATH", str(DEFAULT_DB))).resolve()

@contextmanager
def connect(path=None):
    target = Path(path or db_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        yield connection
    finally:
        connection.close()

def initialize(path=None):
    admin_id = bootstrap_admin_id()
    with connect(path) as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))
        job_columns={r['name'] for r in db.execute('PRAGMA table_info(wiki_jobs)')}
        if 'scope' not in job_columns:
            db.execute("ALTER TABLE wiki_jobs ADD COLUMN scope TEXT NOT NULL DEFAULT 'all' CHECK(scope IN ('all','common','expert'))")
        if 'expert_id' not in job_columns:
            db.execute("ALTER TABLE wiki_jobs ADD COLUMN expert_id TEXT NOT NULL DEFAULT ''")
        # Invalidate the read cache only for changes to canonical knowledge.
        # Expert creation and background job updates do not reload paper contents.
        for table in ('papers','paper_documents','pages','paper_topics','claims','evidence_spans',
                      'claim_evidence','measurements','claim_measurements','measurement_evidence',
                      'condition_values','knowledge_pages','knowledge_links'):
            for event in ('INSERT','UPDATE','DELETE'):
                db.execute(f'CREATE TRIGGER IF NOT EXISTS wiki_clock_{table}_{event} AFTER {event} ON {table} BEGIN UPDATE wiki_corpus_clock SET revision=revision+1 WHERE id=1; END')
        db.execute("INSERT OR IGNORE INTO schema_meta VALUES(1,?)", (now(),))
        # Seed only once. SSO profile sync must never reset an assigned role.
        db.execute("INSERT OR IGNORE INTO users(employee_id,role,created_at,updated_at) VALUES(?,'admin',?,?)", (admin_id,now(),now()))
        from .terminology import seed
        seed(db)
        for id, name, overview, tag in (
            ("dram", "DRAM", "DRAM 소자·retention·reliability", "DRAM"),
            ("nand", "NAND · FeNAND", "NAND와 FeNAND의 소자·CIM", "NAND"),
            ("nor", "NOR Flash", "NOR 구조·sensing·시스템 추정", "NOR"),
            ("ferroelectric", "Ferroelectric Memory", "FRAM·FeFET의 polarization, endurance, retention 및 imprint", "FeRAM · FeFET"),
            ("emerging", "PCM · Selector Memory", "PCM·OTS·selector-only memory의 소자와 동작", "PCM · OTS"),
            ("cim", "CIM · In-memory Computing", "메모리 기반 연산·search·attention의 소자 및 시스템 평가", "CIM · PIM"),
            ("logic", "GAA · CFET · SRAM", "GAA·CFET 소자, SRAM 및 DTCO", "Logic · SRAM"),
            ("architecture", "AI Systems · Integration", "AI 시스템의 memory hierarchy·integration·전력과 설계 전망", "Architecture"),
            ("modeling", "Device Modeling", "소자 물리·compact model·SPICE 및 모델 검증", "Modeling"),
        ):
            db.execute("INSERT OR IGNORE INTO expert_categories VALUES(?,?,?,?)", (id,name,overview,tag))
        db.commit()

def sync_wiki_memberships(db):
    """Reuse a Wiki topic only when every cited paper belongs to the expert."""
    from .wiki_engine import enqueue
    job_id=enqueue(db)
    db.execute("DELETE FROM expert_knowledge_pages WHERE expert_id IN (SELECT expert_id FROM expert_deletions)")
    db.execute("""DELETE FROM expert_knowledge_pages WHERE EXISTS (
      SELECT 1 FROM knowledge_links l JOIN claims c ON c.id=l.claim_id
      WHERE l.page_id=expert_knowledge_pages.page_id AND NOT EXISTS (
        SELECT 1 FROM expert_papers ep WHERE ep.paper_id=c.paper_id AND ep.expert_id=expert_knowledge_pages.expert_id))""")
    db.execute("""INSERT OR IGNORE INTO expert_knowledge_pages(expert_id,page_id)
      SELECT e.id,k.id FROM expert_categories e CROSS JOIN knowledge_pages k
      WHERE e.id NOT IN (SELECT expert_id FROM expert_deletions)
      AND EXISTS(SELECT 1 FROM knowledge_links l WHERE l.page_id=k.id)
      AND NOT EXISTS(SELECT 1 FROM knowledge_links l JOIN claims c ON c.id=l.claim_id
        WHERE l.page_id=k.id AND NOT EXISTS(SELECT 1 FROM expert_papers ep WHERE ep.paper_id=c.paper_id AND ep.expert_id=e.id))""")
    return job_id

