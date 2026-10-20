"""Rebuild only derived search data, with a backup and atomic corpus update."""
import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .chunking import load_chunk_policy, reindex_document, prepare_document_index, IndexInputsChanged
from .pilot_db import connect, db_path, initialize
from .embedding_client import EmbeddingClient, EmbeddingError


def check_connection(policy=None, *, database=None):
    """Explicit opt-in probe using two synthetic sentences, not corpus content."""
    policy = policy or load_chunk_policy()
    if policy.mode != "semantic":
        raise EmbeddingError("Embedding is not configured; no API request was made")
    texts = [policy.embedding.input_prefix + text for text in (
        "A DRAM cell retains stored charge.", "A NAND device erases a block.")]
    if any(len(policy.splitter(policy.embedding.max_input_tokens).chunks(text)) != 1 for text in texts):
        raise EmbeddingError("Embedding probe exceeds configured input budget")
    from .wiki_retrieval import GatedEmbeddingClient
    client = GatedEmbeddingClient(policy.embedding,database)
    vectors = client.embed(texts)
    return {"status": "ok", "api_format": policy.embedding.api_format, "vectors": len(vectors),
            "dimensions": len(vectors[0]), "tokenizer": policy.metadata()["tokenizer"],
            "input": "synthetic test sentences only; corpus unchanged"}


def reindex(path=None):
    path = Path(path or db_path()).resolve()
    if not path.is_file():
        raise FileNotFoundError("An existing corpus database is required")
    policy = load_chunk_policy()
    # Validate tokenizer availability before touching the corpus.
    policy.splitter(policy.max_tokens)
    backup = path.parent / "backups" / (path.stem + "-before-chunking-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite3")
    backup.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
    initialize(path)
    with connect(path) as db:
        run_ids = [row[0] for row in db.execute("SELECT current_run_id FROM paper_documents WHERE current_run_id IS NOT NULL ORDER BY current_run_id")]
        prepared = [prepare_document_index(db, run_id, policy) for run_id in run_ids]
        db.execute("BEGIN IMMEDIATE")
        try:
            current_ids = [row[0] for row in db.execute("SELECT current_run_id FROM paper_documents WHERE current_run_id IS NOT NULL ORDER BY current_run_id")]
            if current_ids != run_ids:
                raise IndexInputsChanged("Corpus changed during index preparation; retry")
            results = [reindex_document(db, plan["run_id"], policy, prepared=plan) for plan in prepared]
            from .knowledge_build import refresh_local_index
            refresh_local_index(db)
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Foreign key validation failed")
            db.commit()
        except Exception:
            db.rollback()
            raise
    return {"mode": policy.mode, "documents": len(results), "rebuilt": sum(r["status"] == "indexed" for r in results),
            "chunks": sum(r["chunks"] for r in results), "backup": str(backup), "policy": policy.metadata()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path)
    parser.add_argument("--check", action="store_true", help="Probe the configured API with synthetic text; do not read or reindex papers")
    args = parser.parse_args()
    try:
        print(json.dumps(check_connection(database=args.db) if args.check else reindex(args.db), ensure_ascii=False))
    except EmbeddingError as exc:
        parser.exit(2, str(exc) + "\n")
