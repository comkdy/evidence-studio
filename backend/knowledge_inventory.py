"""Read shared knowledge inventory in the caller's canonical-data transaction."""
from __future__ import annotations

import json


def reusable_common_wiki_ids(db, paper_ids):
    """Return current common source/topic pages wholly covered by these papers.

    This is the preview inventory for a proposed expert, so expert navigation
    pages, parts, and existing experts' specialization notes are not counted.
    It counts current, readable pages (including synthesis awaiting an LLM),
    not completed model syntheses. A pre-migration database retains its legacy
    linked-page count until the Markdown catalogue is populated.
    """
    allowed = set(paper_ids)
    if not allowed:
        return []
    catalogue = {r['id']: dict(r) for r in db.execute('SELECT * FROM wiki_files')}
    if not catalogue:
        linked = {}
        for row in db.execute('''SELECT l.page_id,c.paper_id FROM knowledge_links l
                                 JOIN claims c ON c.id=l.claim_id'''):
            linked.setdefault(row['page_id'], set()).add(row['paper_id'])
        return sorted(page_id for page_id, refs in linked.items() if refs <= allowed)

    from .wiki_engine import COMMON_CONTENT_KINDS, _full_snapshot, current
    from .wiki_store import WikiError, WikiStore

    # A separate snapshot connection would miss changes still being committed by
    # ingestion or expert creation. Use the same canonical rows as membership.
    state = _full_snapshot(connection=db)
    database = next(r['file'] for r in db.execute('PRAGMA database_list') if r['name'] == 'main')
    if not database:
        return []  # An in-memory catalogue has no persisted Markdown location.
    store = WikiStore(database)
    readable = {}

    def verified(row):
        if row['id'] not in readable:
            # Mark first to fail closed on any dependency cycle. current() also
            # detects cycles; caching prevents repeated file reads across topics.
            readable[row['id']] = False
            try:
                store.read(row)
            except (WikiError, OSError, UnicodeError):
                return False
            readable[row['id']] = all(
                dep in catalogue and verified(catalogue[dep])
                for dep in json.loads(row['refs_json'])['dependencies']
            )
        return readable[row['id']]

    result = []
    for row in catalogue.values():
        if row['scope'] != 'common' or row['expert_id'] or row['kind'] not in COMMON_CONTENT_KINDS:
            continue
        refs = set(json.loads(row['refs_json'])['paper_ids'])
        if refs and refs <= allowed and current(row, state, catalogue) and verified(row):
            result.append(row['id'])
    return sorted(result)
