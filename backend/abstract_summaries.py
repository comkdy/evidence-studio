"""Import and serve source-bound Korean abstract summaries; no model API calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .pilot_db import connect, db_path, initialize, now

PROMPT_VERSION = "abstract-ko-v1"


def source_fingerprint(title, abstract):
    source = json.dumps([title, abstract], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def attach_summaries(db, papers):
    """A changed source never silently retains an old displayed summary."""
    if not papers:
        return
    ids = [p["id"] for p in papers]
    saved = {r["paper_id"]: r for r in db.execute(
        "SELECT * FROM paper_abstract_summaries WHERE paper_id IN (" + ",".join("?" for _ in ids) + ")", ids
    )}
    for paper in papers:
        item = saved.get(paper["id"])
        paper["abstract_summary"] = None
        if item and item["source_fingerprint"] == source_fingerprint(paper["title"], paper["abstract"]):
            paper["abstract_summary"] = {
                "text": item["summary_ko"], "language": "ko", "source": "abstract",
                "producer": item["producer"], "prompt_version": item["prompt_version"],
                "created_at": item["created_at"],
            }


def apply_summaries(payload_path, target=None):
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    producer = payload.get("producer", "")
    version = payload.get("prompt_version", "")
    if not isinstance(producer, str) or not producer.strip() or len(producer) > 120 or version != PROMPT_VERSION:
        raise ValueError("A producer and supported abstract summary prompt version are required")
    entries = payload.get("papers")
    if not isinstance(entries, list) or not entries:
        raise ValueError("No abstract summaries supplied")
    with connect(target) as db:
        db.execute("BEGIN IMMEDIATE")
        seen, prepared = set(), []
        for entry in entries:
            paper_id, text = entry.get("paper_id"), entry.get("summary_ko")
            if not isinstance(paper_id, str) or paper_id in seen:
                raise ValueError("Duplicate or invalid paper ID")
            seen.add(paper_id)
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 600 or not re.search("[가-힣]", text):
                raise ValueError("A concise Korean abstract summary is required")
            paper = db.execute("SELECT title,abstract FROM papers WHERE id=?", (paper_id,)).fetchone()
            if not paper or not paper["abstract"].strip():
                raise ValueError("A paper with an extracted abstract is required")
            fingerprint = source_fingerprint(paper["title"], paper["abstract"])
            if entry.get("source_fingerprint") != fingerprint:
                raise ValueError("Abstract source has changed; read it again before summarizing")
            prepared.append((paper_id, text.strip(), fingerprint, producer, version))
        changed = 0
        for values in prepared:
            previous = db.execute("SELECT paper_id,summary_ko,source_fingerprint,producer,prompt_version FROM paper_abstract_summaries WHERE paper_id=?", (values[0],)).fetchone()
            if previous and tuple(previous) == values:
                continue
            db.execute("""INSERT INTO paper_abstract_summaries VALUES(?,?,?,?,?,?)
                ON CONFLICT(paper_id) DO UPDATE SET summary_ko=excluded.summary_ko,
                source_fingerprint=excluded.source_fingerprint,producer=excluded.producer,
                prompt_version=excluded.prompt_version,created_at=excluded.created_at""", (*values, now()))
            changed += 1
        db.commit()
    return {"written": changed, "unchanged": len(prepared) - changed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("--db", type=Path, default=db_path())
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error("Ingest the source papers into an existing DB first")
    # Preserve the prior DB before adding the derived summary table or replacing content.
    backup_dir = args.db.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_dir / (args.db.stem + "-before-abstract-summaries-" + stamp + ".sqlite3")
    with connect(args.db) as db, sqlite3.connect(backup) as destination:
        db.backup(destination)
    initialize(args.db)
    print(json.dumps({**apply_summaries(args.payload, args.db), "backup": str(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
