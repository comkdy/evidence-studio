PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS papers (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, abstract TEXT NOT NULL DEFAULT '',
 title_source TEXT NOT NULL, metadata_status TEXT NOT NULL DEFAULT 'unreviewed',
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_abstract_summaries (
 paper_id TEXT PRIMARY KEY REFERENCES papers(id),
 summary_ko TEXT NOT NULL CHECK(length(trim(summary_ko)) BETWEEN 1 AND 600),
 source_fingerprint TEXT NOT NULL, producer TEXT NOT NULL,
 prompt_version TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_documents (
 id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id),
 sha256 TEXT NOT NULL UNIQUE, byte_size INTEGER NOT NULL,
 page_count INTEGER NOT NULL, current_run_id TEXT
);
CREATE TABLE IF NOT EXISTS document_sources (
 path TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES paper_documents(id),
 filename TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extraction_runs (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES paper_documents(id),
 parser_version TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('ready','partial','needs_review')),
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES extraction_runs(id),
 page_number INTEGER NOT NULL CHECK(page_number>0), text TEXT NOT NULL,
 method TEXT NOT NULL, status TEXT NOT NULL, char_count INTEGER NOT NULL,
 UNIQUE(run_id,page_number)
);
CREATE TABLE IF NOT EXISTS search_chunks (
 id TEXT PRIMARY KEY, page_id TEXT NOT NULL REFERENCES pages(id),
 start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL, text TEXT NOT NULL,
 CHECK(start_offset>=0 AND end_offset>start_offset)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(chunk_id UNINDEXED, title, body, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS search_contexts (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES extraction_runs(id),
 section TEXT NOT NULL, text TEXT NOT NULL, source_spans_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_chunk_details (
 chunk_id TEXT PRIMARY KEY REFERENCES search_chunks(id) ON DELETE CASCADE,
 run_id TEXT NOT NULL REFERENCES extraction_runs(id),
 parent_id TEXT NOT NULL REFERENCES search_contexts(id), ordinal INTEGER NOT NULL,
 section TEXT NOT NULL, context_text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_chunk_spans (
 chunk_id TEXT NOT NULL REFERENCES search_chunks(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, page_id TEXT NOT NULL REFERENCES pages(id),
 start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
 chunk_start INTEGER NOT NULL, chunk_end INTEGER NOT NULL, page_number INTEGER NOT NULL,
 PRIMARY KEY(chunk_id,ordinal),
 CHECK(start_offset>=0 AND end_offset>start_offset AND chunk_start>=0 AND chunk_end>chunk_start)
);
CREATE TABLE IF NOT EXISTS search_index_state (
 run_id TEXT PRIMARY KEY REFERENCES extraction_runs(id), fingerprint TEXT NOT NULL,
 policy_json TEXT NOT NULL, indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chunk_details_run ON search_chunk_details(run_id);
CREATE TABLE IF NOT EXISTS chunk_embedding_cache (
 cache_key TEXT PRIMARY KEY, model_fingerprint TEXT NOT NULL, input_sha256 TEXT NOT NULL,
 vector_json TEXT NOT NULL, dimensions INTEGER NOT NULL CHECK(dimensions>0), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chunk_embedding_model ON chunk_embedding_cache(model_fingerprint);
CREATE TABLE IF NOT EXISTS search_semantic_audits (
 run_id TEXT PRIMARY KEY REFERENCES extraction_runs(id), details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_topics (
 paper_id TEXT NOT NULL REFERENCES papers(id), tag TEXT NOT NULL, basis TEXT NOT NULL,
 method TEXT NOT NULL DEFAULT 'rules-v1', PRIMARY KEY(paper_id,tag)
);
CREATE TABLE IF NOT EXISTS ingestion_jobs (
 id INTEGER PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT, status TEXT NOT NULL,
 detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processing_tasks (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL, label TEXT NOT NULL,
 identity TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'queued',
 stage TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '',
 paper_id TEXT, wiki_job_id INTEGER, needs_review INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(kind,target)
);
CREATE TABLE IF NOT EXISTS raw_watcher_status (
 id INTEGER PRIMARY KEY CHECK(id=1), heartbeat_at TEXT NOT NULL,
 checked_at TEXT, state TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS curation_runs (
 id TEXT PRIMARY KEY, producer TEXT NOT NULL, method TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_spans (
 id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id),
 page_id TEXT NOT NULL REFERENCES pages(id), start_offset INTEGER NOT NULL,
 end_offset INTEGER NOT NULL, quote TEXT NOT NULL, locator TEXT NOT NULL,
 UNIQUE(id,paper_id), CHECK(end_offset>start_offset)
);
CREATE TABLE IF NOT EXISTS claims (
 id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id),
 curation_id TEXT NOT NULL REFERENCES curation_runs(id), summary TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('observation','estimate','interpretation','limitation')),
 scope TEXT NOT NULL, source_status TEXT NOT NULL DEFAULT 'quote_matched',
 review_status TEXT NOT NULL DEFAULT 'human_review_pending',
 UNIQUE(id,paper_id)
);
CREATE TABLE IF NOT EXISTS claim_evidence (
 claim_id TEXT NOT NULL, span_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 PRIMARY KEY(claim_id,span_id),
 FOREIGN KEY(claim_id,paper_id) REFERENCES claims(id,paper_id),
 FOREIGN KEY(span_id,paper_id) REFERENCES evidence_spans(id,paper_id)
);
CREATE TABLE IF NOT EXISTS measurements (
 id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id),
 curation_id TEXT NOT NULL REFERENCES curation_runs(id),
 label TEXT NOT NULL, raw_value TEXT NOT NULL, operator TEXT NOT NULL,
 numeric_value REAL, unit TEXT NOT NULL, sample TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('measured','simulated','estimated')),
 UNIQUE(id,paper_id)
);
CREATE TABLE IF NOT EXISTS claim_measurements (
 claim_id TEXT NOT NULL, measurement_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 role TEXT NOT NULL DEFAULT 'reported-result', PRIMARY KEY(claim_id,measurement_id),
 FOREIGN KEY(claim_id,paper_id) REFERENCES claims(id,paper_id),
 FOREIGN KEY(measurement_id,paper_id) REFERENCES measurements(id,paper_id)
);
CREATE TABLE IF NOT EXISTS measurement_evidence (
 measurement_id TEXT NOT NULL, span_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 PRIMARY KEY(measurement_id,span_id),
 FOREIGN KEY(measurement_id,paper_id) REFERENCES measurements(id,paper_id),
 FOREIGN KEY(span_id,paper_id) REFERENCES evidence_spans(id,paper_id)
);
CREATE TABLE IF NOT EXISTS condition_values (
 id TEXT PRIMARY KEY, measurement_id TEXT NOT NULL, paper_id TEXT NOT NULL,
 name TEXT NOT NULL, raw_value TEXT, span_id TEXT,
 FOREIGN KEY(measurement_id,paper_id) REFERENCES measurements(id,paper_id),
 FOREIGN KEY(span_id,paper_id) REFERENCES evidence_spans(id,paper_id),
 CHECK((raw_value IS NULL AND span_id IS NULL) OR (raw_value IS NOT NULL AND span_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS expert_categories (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, overview TEXT NOT NULL, topic_tag TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expert_deletions (
 expert_id TEXT PRIMARY KEY REFERENCES expert_categories(id), deleted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expert_papers (
 expert_id TEXT NOT NULL REFERENCES expert_categories(id), paper_id TEXT NOT NULL REFERENCES papers(id),
 method TEXT NOT NULL, PRIMARY KEY(expert_id,paper_id)
);
CREATE TABLE IF NOT EXISTS knowledge_pages (
 id TEXT PRIMARY KEY, expert_id TEXT NOT NULL REFERENCES expert_categories(id),
 curation_id TEXT NOT NULL REFERENCES curation_runs(id), title TEXT NOT NULL,
 body TEXT NOT NULL, limitation TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_links (
 page_id TEXT NOT NULL REFERENCES knowledge_pages(id),
 claim_id TEXT NOT NULL REFERENCES claims(id), PRIMARY KEY(page_id,claim_id)
);
CREATE TABLE IF NOT EXISTS expert_knowledge_pages (
 expert_id TEXT NOT NULL REFERENCES expert_categories(id),
 page_id TEXT NOT NULL REFERENCES knowledge_pages(id),
 PRIMARY KEY(expert_id,page_id)
);
CREATE INDEX IF NOT EXISTS ix_pages_run ON pages(run_id);
CREATE TABLE IF NOT EXISTS page_review_requests (
 id TEXT PRIMARY KEY, page_id TEXT NOT NULL REFERENCES pages(id),
 revision TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('correct','verify','exclude','reopen')),
 proposed_text TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL,
 actor_id TEXT NOT NULL REFERENCES users(employee_id),
 state TEXT NOT NULL CHECK(state IN ('pending','applied','rejected')),
 reviewer_id TEXT REFERENCES users(employee_id), decision_note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, decided_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_page_review_requests_page ON page_review_requests(page_id,created_at);
CREATE TABLE IF NOT EXISTS page_review_changes (
 request_id TEXT PRIMARY KEY REFERENCES page_review_requests(id),
 page_id TEXT NOT NULL REFERENCES pages(id),
 before_text TEXT NOT NULL, before_status TEXT NOT NULL, before_method TEXT NOT NULL,
 after_text TEXT NOT NULL, after_status TEXT NOT NULL, after_method TEXT NOT NULL,
 span_offsets_json TEXT NOT NULL, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_classifications (
 paper_id TEXT PRIMARY KEY REFERENCES papers(id), facets_json TEXT NOT NULL,
 fingerprint TEXT NOT NULL, method TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expert_policies (
 expert_id TEXT PRIMARY KEY REFERENCES expert_categories(id), policy_json TEXT NOT NULL,
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chunks_page ON search_chunks(page_id);
CREATE INDEX IF NOT EXISTS ix_claims_paper ON claims(paper_id);
CREATE INDEX IF NOT EXISTS ix_spans_page ON evidence_spans(page_id);
CREATE TABLE IF NOT EXISTS paper_metadata_changes (
 id INTEGER PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id),
 curation_id TEXT NOT NULL REFERENCES curation_runs(id),
 field TEXT NOT NULL, previous_value TEXT NOT NULL, corrected_value TEXT NOT NULL,
 source_span_id TEXT NOT NULL REFERENCES evidence_spans(id)
);

-- Markdown is authoritative. These tables hold publication metadata, not Wiki bodies.
CREATE TABLE IF NOT EXISTS wiki_files (
 id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, scope TEXT NOT NULL,
 expert_id TEXT REFERENCES expert_categories(id), kind TEXT NOT NULL, title TEXT NOT NULL,
 input_hash TEXT NOT NULL, content_hash TEXT NOT NULL, revision INTEGER NOT NULL,
 refs_json TEXT NOT NULL, status TEXT NOT NULL, producer TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(page_id UNINDEXED, title, body, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS wiki_revisions (
 page_id TEXT NOT NULL, revision INTEGER NOT NULL, path TEXT NOT NULL,
 content_hash TEXT NOT NULL, input_hash TEXT NOT NULL, refs_json TEXT NOT NULL,
 producer TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(page_id, revision)
);
CREATE TABLE IF NOT EXISTS wiki_jobs (
 id INTEGER PRIMARY KEY, reason TEXT NOT NULL, created_at TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', finished_at TEXT, error TEXT NOT NULL DEFAULT '',
 scope TEXT NOT NULL DEFAULT 'all' CHECK(scope IN ('all','common','expert')),
 expert_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS wiki_worker_lease (
 id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wiki_lint_runs (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, finished_at TEXT,
 state TEXT NOT NULL, model TEXT NOT NULL, fingerprint TEXT NOT NULL,
 report_path TEXT NOT NULL, findings_json TEXT NOT NULL, llm_checked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS wiki_corpus_clock (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
INSERT OR IGNORE INTO wiki_corpus_clock VALUES(1,0);
CREATE TABLE IF NOT EXISTS wiki_lint_checks (
 page_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, checked_at TEXT NOT NULL,
 model TEXT NOT NULL, findings_json TEXT NOT NULL, next_offset INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS retrieval_units (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, reference_id TEXT NOT NULL,
 expert_id TEXT, paper_ids_json TEXT NOT NULL, title TEXT NOT NULL,
 text TEXT NOT NULL, metadata_json TEXT NOT NULL, input_hash TEXT NOT NULL,
 vector_json TEXT, model_fingerprint TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts USING fts5(unit_id UNINDEXED,title,body,tokenize='unicode61');
CREATE INDEX IF NOT EXISTS ix_retrieval_kind ON retrieval_units(kind,expert_id);
CREATE TABLE IF NOT EXISTS wiki_topic_requests (
 id TEXT PRIMARY KEY, expert_id TEXT NOT NULL REFERENCES expert_categories(id),
 title TEXT NOT NULL, source_ids_json TEXT NOT NULL, created_at TEXT NOT NULL
);

-- Identity comes from verified SSO; application roles are controlled locally.
CREATE TABLE IF NOT EXISTS users (
 employee_id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', team TEXT NOT NULL DEFAULT '',
 role TEXT NOT NULL CHECK(role IN ('admin','superuser','user')),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 profile_source TEXT NOT NULL DEFAULT 'pending', profile_synced_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS user_audit (
 id INTEGER PRIMARY KEY, actor_id TEXT NOT NULL, employee_id TEXT NOT NULL,
 action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at TEXT NOT NULL
);

-- Private, immutable answer snapshots. No mutable claim FK: corrections must not
-- rewrite the evidence that supported an earlier answer. Wiki generation never
-- reads this table, and another user's conversation is not a retrieval source.
CREATE TABLE IF NOT EXISTS answer_runs (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, created_at TEXT NOT NULL,
 question TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('basic','deep')),
 status TEXT NOT NULL, model_identity TEXT NOT NULL,
 policy_hash TEXT NOT NULL, prompt_hash TEXT NOT NULL,
 evidence_json TEXT NOT NULL, trace_json TEXT NOT NULL, result_json TEXT NOT NULL,
 elapsed_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_answer_runs_owner ON answer_runs(owner_id,created_at);

-- Editorial terminology is separate from scientific evidence and source text.
CREATE TABLE IF NOT EXISTS terminology_meta (
  id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL DEFAULT 1,
  seeded INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO terminology_meta(id) VALUES(1);
CREATE TABLE IF NOT EXISTS terminology_entries (
  id TEXT PRIMARY KEY, preferred TEXT NOT NULL, aliases_json TEXT NOT NULL,
  related_json TEXT NOT NULL DEFAULT '[]', scope TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK(status IN ('draft','approved','archived')),
  revision INTEGER NOT NULL, created_by TEXT NOT NULL, updated_by TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terminology_revisions (
  term_id TEXT NOT NULL REFERENCES terminology_entries(id), revision INTEGER NOT NULL,
  snapshot_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(term_id,revision)
);
