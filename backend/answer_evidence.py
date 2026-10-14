"""Assemble bounded answer evidence without model calls or database writes.

Wiki references are page-level dependencies, not sentence-level proof. A Wiki
excerpt is admitted only after its canonical dependencies fit in the packet.
The budget measures serialized characters, not model tokens.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .pilot_db import db_path
from .pilot_api import claim_detail
from .wiki_engine import current
from .wiki_store import WikiError


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _object(value):
    result = json.loads(value) if isinstance(value, str) else value
    if not isinstance(result, dict):
        raise ValueError('Expected an object')
    return result


def _diverse_order(candidates):
    """Diversify nearby ranks without promoting every paper ahead of good hits.

    Papers already offered a slot receive a fixed twelve-position penalty.
    The original rank remains the tie breaker; distant weak hits cannot leapfrog
    the leading candidates merely because they belong to another paper.
    This is a rank heuristic, not a relevance judgment or a guarantee of paper
    coverage; oversized candidates can still fail budget admission. Retrieval
    errors require separate evaluation and cannot be repaired by this ordering.
    """
    remaining, counts = list(candidates), Counter()
    while remaining:
        candidate = min(remaining, key=lambda c: (c['rank'] + (12 if counts[c['paper_ids'][0]] else 0), c['rank']))
        remaining.remove(candidate)
        counts[candidate['paper_ids'][0]] += 1
        yield candidate


class _Loader:
    def __init__(self, db, allowed):
        self.db, self.allowed = db, allowed
        self.pages, self.claims = {}, {}

    def page(self, page_id):
        if page_id not in self.pages:
            row = self.db.execute('''SELECT pg.*,d.paper_id,d.id AS document_id
                FROM pages pg JOIN paper_documents d ON d.current_run_id=pg.run_id
                WHERE pg.id=?''', (page_id,)).fetchone()
            self.pages[page_id] = dict(row) if row else None
        page = self.pages[page_id]
        if not page or page['paper_id'] not in self.allowed or page['status'] != 'ready':
            raise ValueError('Source page is unavailable or outside the selected scope')
        return page

    def source(self, meta, text, *, status='retrieved_span_only', unit_id=None):
        page = self.page(meta['page_id'])
        start = meta.get('offset', meta.get('start_offset'))
        end = meta.get('end_offset', start + len(text) if isinstance(start, int) else None)
        if (type(start) is not int or type(end) is not int or start < 0 or end <= start or end > len(page['text'])
                or not text or page['text'][start:end] != text):
            raise ValueError('Source text no longer matches its exact offsets')
        for name in ('paper_id', 'document_id', 'page_number'):
            if name in meta and meta[name] != page[name]:
                raise ValueError('Source identity differs from its current page')
        key = f'source:{page["id"]}:{start}:{end}'
        value = dict(kind='source_text', paper_id=page['paper_id'], paper_ids=[page['paper_id']],
                     expert_id=None, page_id=page['id'], page_number=page['page_number'],
                     document_id=page['document_id'], offset=start, end_offset=end,
                     text=text, context_status=status)
        if unit_id is not None:
            value['unit_id'] = unit_id
        if meta.get('chunk_id'):
            value['chunk_id'] = meta['chunk_id']
        return key, value

    def claim(self, claim_id):
        if claim_id in self.claims:
            return self.claims[claim_id]
        row = self.db.execute('''SELECT c.*,p.title FROM claims c
            JOIN papers p ON p.id=c.paper_id WHERE c.id=?''', (claim_id,)).fetchone()
        if not row or row['paper_id'] not in self.allowed:
            raise ValueError('Claim is outside the selected scope')
        claim = claim_detail(self.db, row)
        if not claim['sources']:
            raise ValueError('Claim has no source')
        spans = list(claim['sources'])
        for measurement in claim['measurements']:
            if not measurement['sources']:
                raise ValueError('Measurement has no source')
            spans.extend(measurement['sources'])
            for condition in measurement['conditions']:
                if condition['raw_value'] is not None:
                    if not condition['source']:
                        raise ValueError('A stated condition has no source')
                    spans.append(condition['source'])
        for span in spans:
            _, verified = self.source(span, span['quote'], status='claim_quote')
            if verified['paper_id'] != claim['paper_id']:
                raise ValueError('Claim source belongs to a different paper')
            span['document_id'] = verified['document_id']
        claim['document_id'] = claim['sources'][0]['document_id']
        result = ('claim:' + claim_id, dict(kind='claim', claim=claim,
                                          paper_ids=[claim['paper_id']], expert_id=None))
        self.claims[claim_id] = result
        return result

    def passage(self, unit):
        meta = _object(unit['metadata_json'])
        original = self.source(meta, unit['text'], unit_id=unit['id'])
        chunk_id = meta.get('chunk_id')
        if not chunk_id:
            return [original], [original], 'parent_unavailable'
        parent = self.db.execute('''SELECT p.* FROM search_contexts p
            JOIN search_chunk_details d ON d.parent_id=p.id WHERE d.chunk_id=?''',
            (chunk_id,)).fetchone()
        if not parent:
            return [original], [original], 'parent_unavailable'
        start, end = original[1]['offset'], original[1]['end_offset']
        child = self.db.execute('''SELECT 1 FROM search_chunk_spans
            WHERE chunk_id=? AND page_id=? AND start_offset<=? AND end_offset>=?''',
            (chunk_id, meta['page_id'], start, end)).fetchone()
        if not child:
            raise ValueError('Retrieval passage is outside its child chunk')
        try:
            spans = json.loads(parent['source_spans_json'])
            if not isinstance(spans, list) or not spans:
                raise ValueError('Parent has no source mapping')
            expanded = []
            for span in spans:
                page = self.page(span['page_id'])
                if page['run_id'] != parent['run_id']:
                    raise ValueError('Parent refers to a different extraction')
                low, high = span['chunk_start'], span['chunk_end']
                if type(low) is not int or type(high) is not int or not 0 <= low < high <= len(parent['text']):
                    raise ValueError('Parent mapping is outside its text')
                text = page['text'][span['start_offset']:span['end_offset']]
                if parent['text'][low:high] != text:
                    raise ValueError('Parent text no longer matches its source mapping')
                # Merge only adjacent original intervals; never join across page gaps.
                if (expanded and expanded[-1][1]['page_id'] == page['id']
                        and expanded[-1][1]['end_offset'] == span['start_offset']):
                    previous = expanded.pop()[1]
                    span = dict(span, start_offset=previous['offset'])
                    text = previous['text'] + text
                key, ev = self.source(dict(span, chunk_id=chunk_id), text,
                                      status='parent_expanded', unit_id=unit['id'])
                ev['parent_id'] = parent['id']
                expanded.append((key, ev))
            if not any(ev['page_id'] == meta['page_id'] and ev['offset'] <= start
                       and ev['end_offset'] >= end for _, ev in expanded):
                raise ValueError('Parent does not contain the retrieved passage')
            return expanded, [original], None
        except (ValueError, KeyError, TypeError):
            return [original], [original], 'parent_invalid'


def _covering_key(evidence, key, value):
    if key in evidence:
        return key
    if value['kind'] != 'source_text':
        return None
    for existing_key, existing in evidence.items():
        if existing['kind'] == 'source_text':
            spans = [dict(page_id=existing['page_id'], start_offset=existing['offset'],
                          end_offset=existing['end_offset'])]
        elif existing['kind'] == 'claim':
            claim = existing['claim']
            spans = list(claim['sources'])
            for measurement in claim['measurements']:
                spans += measurement['sources']
                spans += [c['source'] for c in measurement['conditions'] if c['source']]
        else:
            continue
        if any(s['page_id'] == value['page_id'] and s['start_offset'] <= value['offset']
               and s['end_offset'] >= value['end_offset'] for s in spans):
            return existing_key
    return None


def build_evidence(ranked, expert_ids, *, store, state, catalogue, budget=32000, database=None):
    """Return shared canonical evidence, eligible Wiki excerpts and diagnostics.

    ``support_ids`` on a Wiki means all its page-level canonical dependencies
    are present; it does not assert that each sentence follows from each source.
    Parent expansion preserves exact per-page intervals. When it cannot fit,
    the unchanged retrieved interval is explicitly marked as partial context.
    """
    if type(budget) is not int or budget < 2:
        raise ValueError('Evidence budget must be an integer of at least two characters')
    selected = list(dict.fromkeys(expert_ids))
    evidence, wikis, source_candidates, wiki_candidates = {}, {}, [], []
    diagnostics = dict(budget_chars=budget, used_chars=2, selected_units=[], dropped_units=[],
                       paper_ids=[], notices=[], parent_expansions=0, partial_contexts=0)
    outcomes = {}

    def drop(unit, reason):
        outcomes.setdefault(unit['id'], reason)

    def add_candidate(unit, pairs, fallback=None, notice=None, rank=0):
        source_candidates.append(dict(unit=unit, pairs=pairs, fallback=fallback, notice=notice,
                                      rank=rank,
                                      paper_ids=sorted({p for _, ev in pairs for p in ev['paper_ids']})))

    target = Path(database or getattr(store, 'database', None) or db_path()).resolve()
    with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        active = {r[0] for r in db.execute('''SELECT id FROM expert_categories
            WHERE id NOT IN (SELECT expert_id FROM expert_deletions)
            AND id NOT IN (SELECT expert_id FROM expert_policies WHERE enabled=0)''')}
        selected = [eid for eid in selected if eid in active and eid in state['experts']]
        allowed = set()
        for eid in selected:
            live = {r[0] for r in db.execute('SELECT paper_id FROM expert_papers WHERE expert_id=?', (eid,))}
            allowed.update(live.intersection(state['experts'][eid]['papers']))
        loader = _Loader(db, allowed)
        seen = set()
        for rank, raw_unit in enumerate(ranked):
            unit = dict(raw_unit)
            if unit['id'] in seen:
                continue
            seen.add(unit['id'])
            try:
                if unit.get('expert_id') not in (None, '', *selected):
                    raise ValueError('Expert-owned retrieval unit is outside scope')
                kind, reference = unit['kind'], unit['reference_id']
                if kind == 'claim':
                    add_candidate(unit, [loader.claim(reference)], rank=rank)
                elif kind in ('passage', 'source_text'):
                    pairs, fallback, notice = loader.passage(unit)
                    add_candidate(unit, pairs, fallback, notice, rank)
                elif kind == 'wiki':
                    row = catalogue.get(reference)
                    if (not row or row['status'] in ('archived', 'needs_source', 'empty')
                            or row.get('expert_id') not in (None, '', *selected)
                            or not current(row, state, catalogue)):
                        raise ValueError('Wiki is stale or outside scope')
                    refs = _object(row['refs_json'])
                    papers = set(refs['paper_ids'])
                    if not papers or not papers.issubset(allowed):
                        raise ValueError('Wiki dependencies are outside scope')
                    # Read dependencies too: a modified source Markdown invalidates
                    # its dependent Wiki even when the catalogue has not caught up.
                    visited = set()
                    def read_verified(item):
                        if item['id'] in visited:
                            return
                        visited.add(item['id'])
                        for dep in _object(item['refs_json']).get('dependencies', {}):
                            read_verified(catalogue[dep])
                        store.read(item)
                    read_verified(row)
                    _, body = store.read(row)
                    metadata = _object(unit.get('metadata_json', '{}'))
                    if (not unit['text'] or unit['text'] not in body
                            or metadata.get('revision', row['revision']) != row['revision']):
                        raise ValueError('Wiki retrieval unit is stale')
                    pairs = [loader.claim(cid) for cid in dict.fromkeys(refs.get('claim_ids', []))]
                    pairs += [loader.source(span, span['quote'], status='wiki_quote')
                              for span in refs.get('source_spans', [])]
                    pairs = list(dict(pairs).items())
                    supported_papers = {pid for _, ev in pairs for pid in ev['paper_ids']}
                    if not pairs or supported_papers != papers:
                        raise ValueError('Wiki has no valid canonical support')
                    # Dependencies are not independent search hits. Defer them
                    # until directly retrieved evidence has had its opportunity,
                    # then admit the complete Wiki bundle or none of it.
                    wiki_candidates.append(dict(unit=unit, row=row, pairs=pairs, paper_ids=sorted(papers)))
                else:
                    raise ValueError('Unknown retrieval unit type')
            except (ValueError, KeyError, TypeError, WikiError):
                drop(unit, 'invalid_or_out_of_scope')

        def admit(pairs):
            pending = dict(evidence)
            for key, ev in pairs:
                if _covering_key(pending, key, ev) is None:
                    pending[key] = ev
            if len(_json(pending)) > budget:
                return False
            evidence.update(pending)
            return True

        for candidate in _diverse_order(source_candidates):
            unit, pairs = candidate['unit'], candidate['pairs']
            notice = candidate['notice']
            expanded = any(ev.get('context_status') == 'parent_expanded' for _, ev in pairs)
            if not admit(pairs):
                if not candidate['fallback'] or not admit(candidate['fallback']):
                    drop(unit, 'budget')
                    continue
                notice = 'parent_budget'
                expanded = False
            outcomes[unit['id']] = 'selected'
            if expanded:
                diagnostics['parent_expansions'] += 1
            if notice:
                diagnostics['partial_contexts'] += 1
                diagnostics['notices'].append('상위 문맥 전체를 제공하지 못한 원문 구간이 있습니다. 해당 구간만으로 실험 조건이 완전하다고 판단하지 마세요.')

        for candidate in wiki_candidates:
            unit, row = candidate['unit'], candidate['row']
            if any(ev['kind'] == 'wiki' and ev['id'] == row['id'] and unit['text'] in ev['text']
                   for ev in evidence.values()):
                outcomes[unit['id']] = 'duplicate'
                continue
            pending = dict(evidence)
            for key, ev in candidate['pairs']:
                if _covering_key(pending, key, ev) is None:
                    pending[key] = ev
            support = [_covering_key(pending, key, ev) for key, ev in candidate['pairs']]
            value = dict(kind='wiki', id=row['id'], title=row['title'], path=row['path'],
                         revision=row['revision'], text=unit['text'], paper_ids=candidate['paper_ids'],
                         expert_id=row.get('expert_id'), support_ids=list(dict.fromkeys(support)),
                         support_scope='page_dependencies', unit_id=unit['id'])
            missing_support = any(_covering_key(evidence, key, ev) is None for key, ev in candidate['pairs'])
            if not admit(candidate['pairs'] + [('wiki:' + row['id'] + ':' + unit['id'], value)]):
                outcomes[unit['id']] = 'wiki_support_budget' if missing_support else 'wiki_budget'
                continue
            outcomes[unit['id']] = 'selected'
            wikis[row['id']] = {name: row[name] for name in ('id', 'title', 'revision', 'path')}

    diagnostics['used_chars'] = len(_json(evidence))
    diagnostics['paper_ids'] = sorted({pid for ev in evidence.values() for pid in ev['paper_ids']})
    diagnostics['selected_units'] = [key for key, reason in outcomes.items() if reason == 'selected']
    diagnostics['dropped_units'] = [dict(id=key, reason=reason) for key, reason in outcomes.items() if reason != 'selected']
    diagnostics['notices'] = list(dict.fromkeys(diagnostics['notices']))
    return dict(evidence=evidence, wikis=list(wikis.values()), diagnostics=diagnostics)
