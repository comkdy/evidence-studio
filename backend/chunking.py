"""Structural or embedding-semantic chunks with immutable multi-page source locations."""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path

from dotenv import dotenv_values
from semantic_text_splitter import TextSplitter

from .pilot_db import ROOT, now
from .ai_settings import load_ai_settings
from .embedding_client import CachedEmbedder, EmbeddingClient, EmbeddingConfig, EmbeddingError
from .semantic_chunking import VERSION as SEMANTIC_VERSION, semantic_ranges

VERSION = "section-parent-child-v2"
HEADING = re.compile(r"^(?:(?:[IVX]+|\d+(?:\.\d+)*|[A-Z])[.)]?\s+)?(?:abstract|introduction|background|related work|methods?|methodology|experimental(?:\s+\w+){0,3}|device fabrication|results?(?: and discussion)?|discussion|conclusions?|summary|references|acknowledg(?:e)?ments?)\s*[:.]?$", re.I)
NUMBERED_HEADING = re.compile(r"^(?:[IVX]+[.]|\d+(?:\.\d+)*[.)]?)\s+[A-Z][A-Za-z0-9 ,/&()\-]{2,90}$")


@dataclass(frozen=True)
class ChunkPolicy:
    max_tokens: int = 512
    overlap_tokens: int = 48
    parent_max_tokens: int = 2048
    tokenizer_json: str = ""
    mode: str = "structural"
    embedding: EmbeddingConfig | None = field(default=None, repr=False)
    semantic_percentile: float = 90
    semantic_min_distance: float = 0.12
    semantic_buffer_sentences: int = 1

    def __post_init__(self):
        if not 128 <= self.max_tokens <= 8192:
            raise ValueError("CHUNK_MAX_TOKENS must be between 128 and 8192")
        if not 0 <= self.overlap_tokens < self.max_tokens - 80:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be smaller than the body token budget")
        if not self.max_tokens <= self.parent_max_tokens <= 32768:
            raise ValueError("CHUNK_PARENT_MAX_TOKENS must be >= CHUNK_MAX_TOKENS and <= 32768")
        if self.mode not in ("structural", "semantic") or (self.mode == "semantic" and not self.embedding):
            raise EmbeddingError("Semantic chunking requires a configured embedding service")
        if not 0 <= self.semantic_percentile <= 100 or not 0 <= self.semantic_min_distance <= 2 or not 0 <= self.semantic_buffer_sentences <= 2:
            raise ValueError("Invalid semantic boundary settings")

    def splitter(self, capacity, overlap=0):
        if self.tokenizer_json:
            data = json.loads(self.tokenizer_json)
            # Never silently undercount because a tokenizer truncates/pads input.
            data["truncation"] = None
            data["padding"] = None
            return TextSplitter.from_huggingface_tokenizer_str(json.dumps(data), capacity, overlap=overlap)
        # Built-in reference BPE; local token counting only, no OpenAI API/model.
        return TextSplitter.from_tiktoken_model("gpt-3.5-turbo", capacity, overlap=overlap)

    def metadata(self):
        metadata = {"version": VERSION, "library": "semantic-text-splitter@" + version("semantic-text-splitter"),
                "max_tokens": self.max_tokens, "overlap_tokens": self.overlap_tokens,
                "parent_max_tokens": self.parent_max_tokens,
                "tokenizer": "huggingface-local:" + hashlib.sha256(self.tokenizer_json.encode()).hexdigest() if self.tokenizer_json else "reference-cl100k_base"}
        if self.mode == "semantic":
            metadata.update({"version": SEMANTIC_VERSION, "mode": "semantic", "sentence_splitter": "pysbd@" + version("pysbd"),
                             "percentile": self.semantic_percentile, "minimum_distance": self.semantic_min_distance,
                             "buffer_sentences": self.semantic_buffer_sentences, "embedding": self.embedding.identity(),
                             "embedding_max_input_tokens": self.embedding.max_input_tokens})
        return metadata


def load_chunk_policy(env_file=None, environ=None):
    values = {**dotenv_values(env_file or ROOT / ".env", encoding="utf-8-sig", interpolate=False),
              **(os.environ if environ is None else environ)}
    path = (values.get("CHUNK_TOKENIZER_PATH") or "").strip()
    tokenizer = ""
    if path:
        target = Path(path)
        if not target.is_absolute():
            target = ROOT / target
        tokenizer = target.read_text(encoding="utf-8-sig")
    requested = (values.get("CHUNKING_MODE") or "auto").strip().lower()
    if requested not in ("auto", "semantic", "structural"):
        raise ValueError("CHUNKING_MODE must be auto, semantic or structural")
    configured = any((values.get(key) or "").strip() for key in ("EMBEDDING_BASE_URL", "EMBEDDING_ENDPOINT", "EMBEDDING_MODEL", "EMBEDDING_API_KEY"))
    mode = "semantic" if requested == "semantic" or (requested == "auto" and configured) else "structural"
    embedding = None
    if mode == "semantic":
        api_format = (values.get("EMBEDDING_API_FORMAT") or "company").strip()
        contract_path = (values.get("EMBEDDING_CONTRACT_PATH") or "").strip()
        contract_json = (ROOT / contract_path).read_text(encoding="utf-8-sig") if contract_path else ""
        embedding = EmbeddingConfig(load_ai_settings(env_file, environ).embedding,
            revision=(values.get("EMBEDDING_MODEL_REVISION") or "").strip(),
            input_prefix=values.get("EMBEDDING_INPUT_PREFIX") or "",
            max_input_tokens=int(values.get("EMBEDDING_MAX_INPUT_TOKENS") or 512),
            batch_size=int(values.get("EMBEDDING_BATCH_SIZE") or (1 if api_format == "company" else 32)),
            timeout_seconds=float(values.get("EMBEDDING_TIMEOUT_SECONDS") or 30),
            ca_bundle=(values.get("EMBEDDING_CA_BUNDLE") or "").strip(),
            endpoint_url=(values.get("EMBEDDING_ENDPOINT") or "").strip(),
            api_format=api_format,
            auth_header=(values.get("EMBEDDING_AUTH_HEADER") or "Authorization").strip(),
            auth_scheme=(values.get("EMBEDDING_AUTH_SCHEME") if values.get("EMBEDDING_AUTH_SCHEME") is not None else "Bearer").strip(),
            contract_json=contract_json)
    return ChunkPolicy(max_tokens=int(values.get("CHUNK_MAX_TOKENS") or 512),
                       overlap_tokens=int(values.get("CHUNK_OVERLAP_TOKENS") or 48),
                       parent_max_tokens=int(values.get("CHUNK_PARENT_MAX_TOKENS") or 2048),
                       tokenizer_json=tokenizer, mode=mode, embedding=embedding,
                       semantic_percentile=float(values.get("SEMANTIC_BREAKPOINT_PERCENTILE") or 90),
                       semantic_min_distance=float(values.get("SEMANTIC_MIN_DISTANCE") or 0.12),
                       semantic_buffer_sentences=int(values.get("SEMANTIC_BUFFER_SENTENCES") or 1))


def line_ranges(text):
    offset = 0
    for line in text.splitlines(keepends=True):
        yield offset, offset + len(line), line
        offset += len(line)


def sections_from_pages(pages):
    """Conservative text heading/margin detection, not a PDF layout parser."""
    ready = [p for p in pages if p["status"] == "ready"]
    margins = Counter()
    for page in ready:
        lines = [line.strip() for line in page["text"].splitlines() if line.strip()]
        margins.update(set(lines[:2] + lines[-2:]))
    repeated = {line for line, count in margins.items() if len(ready) >= 3 and count >= max(3, len(ready) * .6) and len(line) < 160 and not HEADING.fullmatch(line)}
    title, text, maps = "", "", []

    def finish():
        # Text extraction can place figure captions after the reference heading.
        # Preserve all content; exclusion needs a layout parser's block labels.
        if text.strip():
            return {"title": title, "text": text, "maps": maps}
        return None

    previous = None
    for page in pages:
        if page["status"] != "ready" or (previous is not None and page["page_number"] != previous + 1):
            section = finish()
            if section:
                yield section
            title, text, maps = "", "", []
        previous = page["page_number"]
        if page["status"] != "ready":
            continue
        lines = list(line_ranges(page["text"]))
        nonempty = [i for i, (_, _, line) in enumerate(lines) if line.strip()]
        edge = set(nonempty[:2] + nonempty[-2:])
        for i, (start, end, line) in enumerate(lines):
            stripped = line.strip()
            # Bare page counters are removed only at the outermost nonempty line.
            page_counter = i in (nonempty[:1] + nonempty[-1:]) and (stripped == str(page["page_number"]) or bool(re.fullmatch(r"page\s+\d+(?:\s*/\s*\d+)?", stripped, re.I)))
            if i in edge and (stripped in repeated or page_counter):
                continue
            heading = len(stripped) <= 100 and (HEADING.fullmatch(stripped) or NUMBERED_HEADING.fullmatch(stripped))
            if heading:
                section = finish()
                if section:
                    yield section
                title, text, maps = stripped, "", []
            maps.append({"start": len(text), "end": len(text) + len(line), "page_id": page["id"],
                         "page_number": page["page_number"], "source_start": start})
            text += line
        # Separator belongs to the search representation, not to any source page.
        text += "\n"
    section = finish()
    if section:
        yield section


def source_spans(maps, start, end):
    spans = []
    for part in maps:
        low, high = max(start, part["start"]), min(end, part["end"])
        if low >= high:
            continue
        span = {"page_id": part["page_id"], "page_number": part["page_number"],
                "start_offset": part["source_start"] + low - part["start"],
                "end_offset": part["source_start"] + high - part["start"],
                "chunk_start": low - start, "chunk_end": high - start}
        if spans and spans[-1]["page_id"] == span["page_id"] and spans[-1]["end_offset"] == span["start_offset"] and spans[-1]["chunk_end"] == span["chunk_start"]:
            spans[-1].update(end_offset=span["end_offset"], chunk_end=span["chunk_end"])
        else:
            spans.append(span)
    return spans


def build_chunks(pages, paper_title, policy, embedder=None, diagnostics=None):
    parent_splitter = policy.splitter(policy.parent_max_tokens)
    body_splitter = policy.splitter(policy.max_tokens - 80, policy.overlap_tokens)
    prefix_splitter = policy.splitter(64)
    limit_check = policy.splitter(policy.max_tokens)
    result = []
    if policy.mode == "semantic" and embedder is None:
        embedder = EmbeddingClient(policy.embedding)
    for section in sections_from_pages(pages):
        prefix = prefix_splitter.chunks(paper_title + "\n" + section["title"])[0] if paper_title or section["title"] else ""
        if policy.mode == "semantic":
            ranges, audit = semantic_ranges(section["text"], policy, embedder, section["title"])
            if diagnostics is not None:
                audit.update({"section": section["title"], "source_spans": source_spans(section["maps"], 0, len(section["text"])),
                              "semantic_groups": len(ranges), "token_limit_parent_splits": 0, "token_limit_child_splits": 0})
                diagnostics.append(audit)
        else:
            ranges = [(0, len(section["text"]))]
        parents = []
        for low, high in ranges:
            pieces = parent_splitter.chunk_indices(section["text"][low:high])
            if policy.mode == "semantic" and diagnostics is not None:
                audit["token_limit_parent_splits"] += max(0, len(pieces) - 1)
            parents.extend((low + offset, text) for offset, text in pieces)
        for parent_start, parent in parents:
            parent_spans = source_spans(section["maps"], parent_start, parent_start + len(parent))
            children = []
            for local_start, body in body_splitter.chunk_indices(parent):
                start = parent_start + local_start
                context_text = prefix + "\n\n" + body
                if len(limit_check.chunks(context_text)) != 1:
                    raise ValueError("Context-enriched chunk exceeds tokenizer limit")
                spans = source_spans(section["maps"], start, start + len(body))
                if spans:
                    children.append({"text": body, "context_text": context_text, "spans": spans})
            if children:
                result.append({"text": parent, "section": section["title"], "spans": parent_spans, "children": children})
                if policy.mode == "semantic" and diagnostics is not None:
                    audit["token_limit_child_splits"] += len(children) - 1
    return result


def reindex_document(db, run_id, policy):
    doc = db.execute("""SELECT p.title FROM paper_documents d JOIN papers p ON p.id=d.paper_id
                        WHERE d.current_run_id=?""", (run_id,)).fetchone()
    if not doc:
        raise ValueError("Only the current extraction can be indexed")
    pages = [dict(p) for p in db.execute("SELECT * FROM pages WHERE run_id=? ORDER BY page_number", (run_id,))]
    metadata = json.dumps(policy.metadata(), sort_keys=True)
    fingerprint = hashlib.sha256((metadata + doc["title"] + json.dumps([(p["id"], p["status"], p["text"]) for p in pages])).encode()).hexdigest()
    current = db.execute("SELECT fingerprint FROM search_index_state WHERE run_id=?", (run_id,)).fetchone()
    if current and current[0] == fingerprint:
        return {"status": "unchanged", "chunks": db.execute("SELECT count(*) FROM search_chunk_details WHERE run_id=?", (run_id,)).fetchone()[0]}
    # Cache and index share a savepoint. Failure never publishes a partial index or a structural fallback.
    db.execute("SAVEPOINT chunk_reindex")
    try:
        diagnostics = []
        embedder = CachedEmbedder(db, policy.embedding) if policy.mode == "semantic" else None
        groups = build_chunks(pages, doc["title"], policy, embedder, diagnostics)
        old = "SELECT c.id FROM search_chunks c JOIN pages p ON p.id=c.page_id WHERE p.run_id=?"
        db.execute("DELETE FROM chunk_fts WHERE chunk_id IN (" + old + ")", (run_id,))
        db.execute("DELETE FROM search_chunks WHERE id IN (" + old + ")", (run_id,))
        db.execute("DELETE FROM search_contexts WHERE run_id=?", (run_id,))
        count = 0
        for i, group in enumerate(groups):
            parent_id = "context-" + hashlib.sha256((run_id + fingerprint + str(i)).encode()).hexdigest()[:24]
            db.execute("INSERT INTO search_contexts VALUES(?,?,?,?,?)", (parent_id, run_id, group["section"], group["text"], json.dumps(group["spans"])))
            for child in group["children"]:
                chunk_id = "chunk-" + hashlib.sha256((parent_id + str(count) + child["text"]).encode()).hexdigest()[:24]
                first = child["spans"][0]
                db.execute("INSERT INTO search_chunks VALUES(?,?,?,?,?)", (chunk_id, first["page_id"], first["start_offset"], first["end_offset"], child["text"]))
                db.execute("INSERT INTO search_chunk_details VALUES(?,?,?,?,?,?)", (chunk_id, run_id, parent_id, count, group["section"], child["context_text"]))
                for n, span in enumerate(child["spans"]):
                    db.execute("INSERT INTO search_chunk_spans VALUES(?,?,?,?,?,?,?,?)", (chunk_id, n, span["page_id"], span["start_offset"], span["end_offset"], span["chunk_start"], span["chunk_end"], span["page_number"]))
                db.execute("INSERT INTO chunk_fts(chunk_id,title,body) VALUES(?,?,?)", (chunk_id, doc["title"], group["section"] + "\n" + child["text"]))
                count += 1
        db.execute("INSERT INTO search_index_state VALUES(?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET fingerprint=excluded.fingerprint,policy_json=excluded.policy_json,indexed_at=excluded.indexed_at", (run_id, fingerprint, metadata, now()))
        db.execute("DELETE FROM search_semantic_audits WHERE run_id=?", (run_id,))
        if embedder:
            audit = {"algorithm": SEMANTIC_VERSION, "cache_hits": embedder.cache_hits,
                     "new_vectors": embedder.new_vectors, "sections": diagnostics}
            db.execute("INSERT INTO search_semantic_audits VALUES(?,?,?)", (run_id, json.dumps(audit), now()))
        db.execute("RELEASE chunk_reindex")
    except Exception:
        db.execute("ROLLBACK TO chunk_reindex")
        db.execute("RELEASE chunk_reindex")
        raise
    return {"status": "indexed", "chunks": count, "contexts": len(groups)}
