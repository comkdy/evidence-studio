"""Incremental ingest -> linked Markdown -> expert knowledge, with durable jobs."""
from __future__ import annotations
import argparse
import json
import os
import re
import sqlite3
import logging
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4
from .pilot_db import connect, db_path, initialize, now, write_connection, is_database_busy
from .llm_client import CompanyLLM, LLMError, LLMTimeoutError, LLMCapacityError
from .wiki_store import WikiStore, WikiError, digest, safe_path, atomic_write, parse_markdown
from .wiki_policy import load_prompt
from .wiki_budget import budget_for, extraction_model, page_evidence, source_batches

LEASE_SECONDS = 600
LEASE_RENEW_SECONDS = 30
VERSION = 'markdown-wiki-v2-shared'
CONTENT_KINDS = frozenset(('source', 'topic', 'specialization'))
COMMON_CONTENT_KINDS = frozenset(('source', 'topic'))


def escaped(text):
    return re.sub(r'([\\`*{}\[\]<>#])', r'\\\1', str(text)).replace('\n', ' ')


def link_to(path, target):
    return os.path.relpath(target, Path(path).parent).replace('\\', '/')


def enqueue(db, reason='papers, evidence or expert settings changed', *, scope='all', expert_id=''):
    # Shares the caller's transaction: rolled-back source changes cannot spawn work.
    if scope not in ('all','common','expert') or (scope=='expert') != bool(expert_id):
        raise WikiError('Wiki 갱신 대상을 확인하세요.')
    existing=db.execute("SELECT id FROM wiki_jobs WHERE state='queued' AND scope=? AND expert_id=? LIMIT 1",(scope,expert_id)).fetchone()
    if existing:
        return existing['id']
    return db.execute('INSERT INTO wiki_jobs(reason,created_at,scope,expert_id) VALUES(?,?,?,?)',
                      (reason,now(),scope,expert_id)).lastrowid


def _full_snapshot(database=None, *, connection=None):
    from .pilot_api import claim_detail
    with (nullcontext(connection) if connection is not None else connect(database)) as db:
        papers = {}
        for row in db.execute('SELECT * FROM papers ORDER BY id').fetchall():
            p = dict(row)
            p['claims'] = []
            for claim in db.execute('SELECT * FROM claims WHERE paper_id=? ORDER BY id', (p['id'],)).fetchall():
                c = claim_detail(db, claim)
                c.pop('experts', None)  # Common facts do not depend on expert membership.
                p['claims'].append(c)
            p['documents'] = [dict(x) for x in db.execute('SELECT * FROM paper_documents WHERE paper_id=? ORDER BY id', (p['id'],))]
            p['pages'] = [dict(x) for x in db.execute('SELECT pg.*,d.id AS document_id FROM pages pg JOIN paper_documents d ON pg.run_id=d.current_run_id WHERE d.paper_id=? ORDER BY d.id,pg.page_number', (p['id'],))]
            p['notes'] = [dict(x) for x in db.execute('SELECT DISTINCT k.id,k.title,k.body,k.limitation FROM knowledge_pages k JOIN knowledge_links l ON l.page_id=k.id JOIN claims c ON c.id=l.claim_id WHERE c.paper_id=? ORDER BY k.id', (p['id'],))]
            # A multi-paper note is compiled at the common-topic layer, never copied
            # into a single-paper scope. Current migrated notes all cite one paper.
            p['notes'] = [n for n in p['notes'] if db.execute('SELECT count(DISTINCT c.paper_id) FROM knowledge_links l JOIN claims c ON c.id=l.claim_id WHERE l.page_id=?', (n['id'],)).fetchone()[0] == 1]
            p['tags'] = [x[0] for x in db.execute('SELECT tag FROM paper_topics WHERE paper_id=? ORDER BY tag', (p['id'],))] or ['Other']
            p['hash'] = digest(p)
            papers[p['id']] = p
        experts = {}
        for row in db.execute('SELECT * FROM expert_categories WHERE id NOT IN (SELECT expert_id FROM expert_deletions) AND id NOT IN (SELECT expert_id FROM expert_policies WHERE enabled=0) ORDER BY id'):
            e = dict(row)
            e['papers'] = [x[0] for x in db.execute('SELECT paper_id FROM expert_papers WHERE expert_id=? ORDER BY paper_id', (e['id'],))]
            policy = db.execute('SELECT policy_json FROM expert_policies WHERE expert_id=?', (e['id'],)).fetchone()
            e['policy'] = policy[0] if policy else ''
            e['hash'] = digest(e)
            experts[e['id']] = e
        return {'papers': papers, 'experts': experts}


_paper_cache = {}
_cache_lock = threading.RLock()


def snapshot(database=None):
    """Canonical paper data is loaded once per corpus revision, not per question.

    Expert memberships remain live; changing an expert does not invalidate papers.
    This local SQLite implementation keeps at most two corpus snapshots in memory.
    """
    target = str(Path(database or db_path()).resolve())
    with connect(database) as db:
        revision = db.execute('SELECT revision FROM wiki_corpus_clock WHERE id=1').fetchone()[0]
        with _cache_lock:
            cached = _paper_cache.get(target)
            if not cached or cached[0] != revision:
                papers = _full_snapshot(database)['papers']
                if len(_paper_cache) >= 2:
                    _paper_cache.pop(next(iter(_paper_cache)))
                _paper_cache[target] = (revision, papers)
            papers = _paper_cache[target][1]
        experts = {}
        for row in db.execute('SELECT * FROM expert_categories WHERE id NOT IN (SELECT expert_id FROM expert_deletions) AND id NOT IN (SELECT expert_id FROM expert_policies WHERE enabled=0) ORDER BY id'):
            e = dict(row)
            e['papers'] = [r[0] for r in db.execute('SELECT paper_id FROM expert_papers WHERE expert_id=? ORDER BY paper_id',(e['id'],))]
            policy = db.execute('SELECT policy_json FROM expert_policies WHERE expert_id=?',(e['id'],)).fetchone()
            e['policy'] = policy[0] if policy else ''
            e['hash'] = digest(e)
            experts[e['id']] = e
        return {'papers':papers,'experts':experts}


def base_refs(paper_ids, state, expert_id=None):
    ids = sorted(set(paper_ids))
    return dict(paper_ids=ids, claim_ids=[], source_spans=[], dependencies={},
                source_hashes={i: state['papers'][i]['hash'] for i in ids},
                scope_hash=state['experts'][expert_id]['hash'] if expert_id else '')


def current(row, state, catalogue, visiting=None):
    if row['status'] == 'archived':
        return False
    refs = json.loads(row['refs_json'])
    eid = row['expert_id']
    if eid and (eid not in state['experts'] or refs['scope_hash'] != state['experts'][eid]['hash']):
        return False
    if eid and not set(refs['paper_ids']).issubset(state['experts'][eid]['papers']):
        return False
    if any(i not in state['papers'] or state['papers'][i]['hash'] != h for i, h in refs['source_hashes'].items()):
        return False
    visiting = set(visiting or ())
    if row['id'] in visiting:
        return False
    visiting.add(row['id'])
    for key, fingerprint in refs['dependencies'].items():
        dep = catalogue.get(key)
        if not dep or dep['content_hash'] != fingerprint or not current(dep, state, catalogue, visiting):
            return False
    return True


def merge_refs(items, state, expert_id=None):
    refs = base_refs([p for r in items for p in json.loads(r['refs_json'])['paper_ids']], state, expert_id)
    for row in items:
        child = json.loads(row['refs_json'])
        refs['claim_ids'].extend(child['claim_ids'])
        refs['source_spans'].extend(child['source_spans'])
        refs['dependencies'][row['id']] = row['content_hash']
    refs['claim_ids'] = sorted(set(refs['claim_ids']))
    refs['source_spans'] = list({digest(s): s for s in refs['source_spans']}.values())
    return refs


def validate_sections(result, evidence, allow_empty=False):
    from .pilot_curate import locate_quote
    sections = result.get('sections')
    if not isinstance(sections, list) or len(sections) > 6 or (not sections and not allow_empty):
        raise WikiError('LLM Wiki sections 형식이 올바르지 않습니다.')
    checked = []
    for section in sections:
        heading = section.get('heading') if isinstance(section, dict) else None
        statements = section.get('statements') if isinstance(section, dict) else None
        if not isinstance(heading, str) or not 1 <= len(heading) <= 160 or not isinstance(statements, list) or not 1 <= len(statements) <= 8:
            raise WikiError('LLM Wiki 항목 형식이 올바르지 않습니다.')
        cleaned = []
        for item in statements:
            text = item.get('text') if isinstance(item, dict) else None
            ids = item.get('evidence_ids') if isinstance(item, dict) else None
            if not isinstance(text, str) or not 1 <= len(text) <= 1800 or not isinstance(ids, list) or not ids or len(ids) > 20 or any(not isinstance(i, str) or i not in evidence for i in ids):
                raise WikiError('제공된 근거에 없는 Wiki 진술입니다. 기존 파일을 유지합니다.')
            quotes = item.get('quotes', {})
            if not isinstance(quotes, dict):
                raise WikiError('원문 인용 형식이 올바르지 않습니다.')
            matches = []
            for key in ids:
                e = evidence[key]
                if e['kind'] == 'source_text':
                    quote = quotes.get(key)
                    if not isinstance(quote, str) or len(quote.strip()) < 12:
                        raise WikiError('새 원문에서 추출한 지식에는 정확한 인용이 필요합니다.')
                    try:
                        start, end = locate_quote(e['text'], quote)
                    except ValueError:
                        raise WikiError('Wiki 인용과 원문이 일치하지 않습니다.') from None
                    matches.append(dict(paper_id=e['paper_id'], page_id=e['page_id'], document_id=e['document_id'],
                                        page_number=e['page_number'], start_offset=e['offset']+start,
                                        end_offset=e['offset']+end, quote=e['text'][start:end]))
            cleaned.append(dict(text=text, evidence_ids=list(dict.fromkeys(ids)), source_spans=matches))
        checked.append(dict(heading=heading, statements=cleaned))
    return checked


def render_sections(sections, evidence, path):
    lines = []
    for section in sections:
        lines += ['## ' + escaped(section['heading']), '']
        for item in section['statements']:
            links = []
            for key in item['evidence_ids']:
                e = evidence[key]
                if e['kind'] == 'claim':
                    target = '/api/pilot/claims/' + e['claim']['id']
                    label = e['claim']['id']
                elif e['kind'] == 'wiki':
                    target = link_to(path, e['path'])
                    label = e['title']
                else:
                    target = '/api/pilot/documents/' + e['document_id'] + '/pdf#page=' + str(e['page_number'])
                    label = '원문 p. ' + str(e['page_number'])
                links.append(f'[{escaped(label)}]({target})')
            lines += ['- ' + escaped(item['text']) + ' (' + ' · '.join(links) + ')', '']
            for span in item['source_spans']:
                lines += ['  > ' + escaped(span['quote']), '']
    return '\n'.join(lines)


def compile_inputs(store, llm, page_id, title, evidence, kind, previous_context, prompt):
    from .terminology import snapshot as term_snapshot, prompt_rules, prompt_text
    rules = prompt_rules(json.dumps([title,previous_context,evidence],ensure_ascii=False),
                         term_snapshot(store.database)) if llm.configured and evidence else []
    old = store.record(page_id)
    # Raw extraction reads current originals, not its own previous summary again.
    raw = evidence and all(e['kind']=='source_text' for e in evidence.values())
    previous = store.read(old)[1] if old else ''
    payload = dict(title=title, page_kind=kind, expert_overview=previous_context,
        previous_wiki='' if raw or kind in ('source_part','topic_part') else previous, evidence=evidence,
        coverage={'complete_paper_review': False, 'scientific_review': 'pending',
            'instruction': '제공된 근거에서 연구 목적·핵심 발견·수치·조건·한계를 선별하세요. 모든 주장과 반례를 포함했다고 가정하지 마세요.'})
    payload['output_budget']={'max_tokens':budget_for(llm).output_tokens,
        'instruction':'핵심 항목을 간결하게 작성하세요. 출력 한도에 맞춰 항목 수를 줄이되, 선택한 진술의 수치·단위·적용 조건과 정확한 원문 인용은 보존하세요.'}
    return prompt_text(prompt.text,rules), payload, rules


def fits_compile(store, llm, page_id, title, evidence, kind, prompt, overview=''):
    system, payload, _ = compile_inputs(store,llm,page_id,title,evidence,kind,overview,prompt)
    return budget_for(llm).fits(system,payload)


def compile_page(store, state, llm, page_id, path, title, evidence, refs, *,
                 expert_id=None, kind='topic', previous_context='', reason='knowledge update', prompt=None):
    prompt = prompt or load_prompt('compile')
    refs = dict(refs, policy_hash=prompt.policy_hash, prompt_hash=prompt.fingerprint)
    from .terminology import fingerprint
    system, payload, rules = compile_inputs(store,llm,page_id,title,evidence,kind,previous_context,prompt)
    if rules:refs['terminology']=dict(fingerprint=fingerprint(rules),terms=rules)
    signature = digest([VERSION, title, refs, evidence, prompt.fingerprint, llm.identity if llm.configured else 'awaiting-llm'])
    old = store.record(page_id)
    if old and old['input_hash'] == signature and old['status'] != 'archived':
        store.read(old)
        return old
    if llm.configured and evidence:
        budget = budget_for(llm)
        if not budget.fits(system,payload):
            raise WikiError('Wiki 요청이 모델 입력 한도를 초과합니다. LLM_CONTEXT_TOKENS/tokenizer 또는 기존 Wiki 크기를 확인하세요.')
        result = (llm.complete_json(system,payload,max_output_tokens=budget.output_tokens)
                  if isinstance(llm,CompanyLLM) else llm.complete_json(system,payload))
        sections = validate_sections(result,evidence)
        body = '# ' + escaped(title) + '\n\n' + render_sections(sections, evidence, path)
        refs['source_spans'].extend(s for section in sections for item in section['statements'] for s in item['source_spans'])
        producer, status = 'company-llm:' + llm.service.model, 'ready'
    else:
        # These are explicit evidence excerpts, not a pretend LLM synthesis.
        body = '# ' + escaped(title) + '\n\n'
        body += ('공통 근거를 연결했습니다. 사내 LLM 연결 후 주제별 종합 설명을 작성합니다.\n\n' if evidence else '아직 담당 범위에 편입된 지식이 없습니다.\n\n')
        for e in evidence.values():
            if e['kind'] == 'wiki':
                body += f'## [{escaped(e["title"])}]({link_to(path, e["path"])})\n\n'
                if e.get('facts'):
                    body += '\n'.join('- ' + escaped(t) for t in e['facts'][:2]) + '\n\n'
            elif e['kind'] == 'source_text':
                body += f'[원문 p. {e["page_number"]}](/api/pilot/documents/{e["document_id"]}/pdf#page={e["page_number"]}) · 지식 분석 대기\n\n'
        producer, status = 'evidence-links', 'awaiting_llm' if evidence else 'empty'
    # Signature describes inputs, not newly extracted output spans.
    return store.publish(page_id, path, title, body, refs, signature, scope='expert' if expert_id else 'common',
                         expert_id=expert_id, kind=kind, status=status, producer=producer, reason=reason)


def source_page(store, state, paper, llm, reason, prompt=None):
    pid = paper['id']
    path = f'common/sources/{pid}.md'
    page_id = 'source-' + pid
    refs = base_refs([pid], state)
    refs['claim_ids'] = [c['id'] for c in paper['claims']]
    if paper['claims']:
        body = '# ' + escaped(paper['title']) + '\n\n'
        body += '이 문서는 DB에 선별해 등록한 claim·수치·조건과 원문 인용을 재사용합니다. 논문 전체를 빠짐없이 분석한 기록은 아니며, 과학적 내용 검토는 대기 중입니다.\n\n'
        for note in paper['notes']:
            body += '## ' + escaped(note['title']) + '\n\n' + escaped(note['body']) + '\n\n'
            if note['limitation']:
                body += '**적용 한계:** ' + escaped(note['limitation']) + '\n\n'
        body += '## 선별된 주장·수치와 적용 조건\n\n'
        for c in paper['claims']:
            body += f'### {escaped(c["summary"])}\n\n'
            body += f'유형: {c["kind"]} · 적용 범위: {escaped(c["scope"])}\n\n'
            for m in c['measurements']:
                cond = '; '.join(x['name'] + ': ' + (x['raw_value'] or '미기재') for x in m['conditions'])
                body += f'- **{escaped(m["label"])}**: {escaped(m["raw_value"])} · {m["kind"]} · {escaped(m["sample"])}. {escaped(cond)}\n'
            body += f'\n[claim · {c["id"]}](/api/pilot/claims/{c["id"]})\n\n'
            for s in c['sources']:
                doc = next(d['id'] for d in paper['documents'] if any(p['id'] == s['page_id'] and p['document_id'] == d['id'] for p in paper['pages']))
                body += f'> {escaped(s["quote"])}\n\n[원문 p. {s["page_number"]}](/api/pilot/documents/{doc}/pdf#page={s["page_number"]})\n\n'
        body += '원문 문자열 대조 완료 · 내용의 타당성은 사람 검토 대상입니다.\n'
        return [store.publish(page_id, path, paper['title'], body, refs, digest([VERSION, paper['hash']]),
                              kind='source', producer='curated-db-migration', reason=reason)]
    prompt = prompt or load_prompt('compile')
    # Preserve already valid pre-upgrade source trees without an expensive forced rebuild.
    legacy = reusable_source_tree(store,state,llm,paper,prompt)
    if legacy:
        return legacy
    _, evidence = page_evidence(paper)
    if not evidence:
        return [store.publish(page_id, path, paper['title'], '# '+escaped(paper['title'])+'\n\n원문 추출·OCR 확인 후 지식을 구성합니다.\n',
                              refs, digest([VERSION, paper['hash'], 'needs-source']), kind='source', status='needs_source', reason=reason)]
    extractor = extraction_model(llm)
    # Prefer a single call over making more chunks just to use the smaller model.
    for model in dict.fromkeys((extractor,llm)):
        if fits_compile(store,model,page_id,paper['title'],evidence,'source',prompt):
            return [compile_page(store,state,model,page_id,path,paper['title'],evidence,refs,
                                 kind='source',reason=reason,prompt=prompt)]
    # A tiny light-model context may not even fit policy + citation metadata.
    sample_key=next(iter(evidence))
    sample={sample_key:dict(evidence[sample_key],text=evidence[sample_key]['text'][:80])}
    if extractor is not llm and not fits_compile(store,extractor,'packing-probe',
            paper['title']+' · 근거 묶음',sample,'source_part',prompt):
        extractor=llm
    # Budget the exact IDs/titles that will be sent, including all metadata.
    def identity(ev):
        return 'part-'+digest([paper['id'],list(ev)])[:24]
    def fits(ev):
        return fits_compile(store,extractor,identity(ev),paper['title']+' · 근거 묶음',ev,'source_part',prompt)
    batches = source_batches(paper,budget_for(extractor),fits)
    parts = [compile_page(store,state,extractor,identity(ev),f'common/sources/parts/{identity(ev)}.md',
                         paper['title']+' · 근거 묶음',ev,base_refs([pid],state),
                         kind='source_part',reason=reason,prompt=prompt) for ev in batches]
    intermediates, root = reduce_sources(store,state,llm,parts,page_id,path,paper['title'],
        kind='source',part_kind='source_part',part_dir='common/sources/parts',reason=reason,prompt=prompt)
    return parts + intermediates + [root]


def synthesis_evidence(store,state,items):
    evidence = {}
    for r in items:
        _, body = store.read(r)
        refs = json.loads(r['refs_json'])
        facts = [c['summary'] for p in refs['paper_ids'] for c in state['papers'][p]['claims'] if c['id'] in refs['claim_ids']]
        evidence[r['id']] = dict(kind='wiki', title=r['title'], path=r['path'], text=body,
                                paper_ids=refs['paper_ids'], claim_ids=refs['claim_ids'], facts=facts[:8])
    return evidence


def reusable_source_tree(store,state,llm,paper,prompt):
    return reusable_synthesis(store,state,llm,store.record('source-'+paper['id']),paper['title'],prompt)


def reusable_synthesis(store,state,llm,root,title,prompt):
    if not root or root['status']!='ready':
        return []
    refs = json.loads(root['refs_json'])
    # Reuse the entire valid dependency tree, including pre-upgrade fixed-page trees.
    if not refs['dependencies']:
        return []
    catalogue = {r['id']:r for r in store.rows()}
    if not current(root,state,catalogue):
        return []
    items = [catalogue[key] for key in refs['dependencies']]
    evidence = synthesis_evidence(store,state,items)
    from .terminology import fingerprint
    _, _, rules = compile_inputs(store,llm,root['id'],title,evidence,root['kind'],'',prompt)
    expected = dict(merge_refs(items,state),policy_hash=prompt.policy_hash,prompt_hash=prompt.fingerprint)
    if 'topic_source_order' in refs:expected['topic_source_order']=refs['topic_source_order']
    if rules:expected['terminology']=dict(fingerprint=fingerprint(rules),terms=rules)
    signature=digest([VERSION,title,expected,evidence,prompt.fingerprint,llm.identity if llm.configured else 'awaiting-llm'])
    if root['input_hash']!=signature:
        return []
    active={}
    def visit(row):
        if row['id'] in active:
            return
        store.read(row)
        active[row['id']]=row
        for key in json.loads(row['refs_json'])['dependencies']:
            visit(catalogue[key])
    visit(root)
    return [r for key,r in active.items() if key!=root['id']]+[root]


def synthesize(store, state, llm, items, page_id, path, title, expert_id=None, kind='topic', reason='knowledge update', prompt=None, extra_refs=None):
    evidence = synthesis_evidence(store,state,items)
    refs = merge_refs(items, state, expert_id)
    if extra_refs:
        refs.update(extra_refs)
    if expert_id and not set(refs['paper_ids']).issubset(state['experts'][expert_id]['papers']):
        raise WikiError('전문가 담당 범위를 벗어난 근거입니다.')
    overview = state['experts'][expert_id]['overview'] if expert_id else ''
    return compile_page(store, state, llm, page_id, path, title, evidence, refs, expert_id=expert_id,
                        kind=kind, previous_context=overview, reason=reason, prompt=prompt)


def common_topic(store, state, llm, sources, tag, reason, prompt, heartbeat=lambda: None):
    """One stable topic root covers every source, with bounded intermediate inputs.

    Existing source order is retained; new sources append to it. Full groups keep
    their identity as the corpus grows when their inputs and budgets are unchanged. Intermediate groups are processing units, not scientific subtopics.
    The old small-topic root ID stays valid, preserving edits and revision history.
    """
    key=digest(tag)[:12]
    root_id='common-'+key+'-0-0'
    by_id={row['id']:row for row in sources}
    old=store.record(root_id)
    old_refs=json.loads(old['refs_json']) if old else {}
    previous_order=old_refs.get('topic_source_order',list(old_refs.get('dependencies',{})))
    order=list(dict.fromkeys(i for i in previous_order if i in by_id))
    order.extend(sorted(set(by_id)-set(order)))
    level=[by_id[i] for i in order]
    if old and set(old_refs['paper_ids']) == {p for r in sources for p in json.loads(r['refs_json'])['paper_ids']}:
        cached=reusable_synthesis(store,state,llm,old,tag,prompt)
        if cached:
            return [r for r in cached[:-1] if r['kind']=='topic_part'],cached[-1]
    return reduce_sources(store,state,llm,level,root_id,f'common/topics/{key}-0-0.md',tag,
        kind='topic',part_kind='topic_part',part_dir='common/topics/parts',reason=reason,prompt=prompt,
        heartbeat=heartbeat,extra_refs={'topic_source_order':order})


def reduce_sources(store,state,llm,items,page_id,path,title,*,kind,part_kind,part_dir,reason,prompt,
                   heartbeat=lambda:None,extra_refs=None,max_levels=8):
    """Only introduce intermediate summaries when the full request cannot fit."""
    level=list(items); parts=[]
    for depth in range(max_levels):
        heartbeat()
        evidence=synthesis_evidence(store,state,level)
        if fits_compile(store,llm,page_id,title,evidence,kind,prompt):
            root=synthesize(store,state,llm,level,page_id,path,title,kind=kind,reason=reason,
                            prompt=prompt,extra_refs=extra_refs)
            return parts,root
        groups=[]; group=[]
        def group_info(rows):
            key=page_id+'-group-'+digest([depth,[r['id'] for r in rows]])[:20]
            return key,title+' · 종합 근거'
        def fits(rows):
            key,label=group_info(rows)
            return fits_compile(store,llm,key,label,synthesis_evidence(store,state,rows),part_kind,prompt)
        for row in level:
            if group and not fits(group+[row]):
                groups.append(group);group=[]
            if not fits([row]):
                raise WikiError('단일 Wiki 근거가 모델 입력 한도를 초과합니다. 모델 context/tokenizer 또는 WIKI_OUTPUT_TOKENS를 조정하세요. 원문을 생략하지 않았습니다.')
            group.append(row)
        if group:groups.append(group)
        if len(groups)>=len(level):
            raise WikiError('Wiki 근거를 손실 없이 종합할 입력 여유가 없습니다. 모델 context/tokenizer 설정을 확인하세요.')
        next_level=[]
        for rows in groups:
            if len(rows)==1:
                next_level.extend(rows)  # no pointless one-document intermediate call
                continue
            heartbeat()
            key,label=group_info(rows)
            part=synthesize(store,state,llm,rows,key,f'{part_dir}/{key}.md',label,
                            kind=part_kind,reason=reason,prompt=prompt)
            parts.append(part);next_level.append(part)
        level=next_level
    raise WikiError('Wiki 종합 단계 한도에 도달했습니다. 입력 한도 또는 주제 범위를 확인하세요.')


def reconcile(database=None, llm=None, reason='knowledge update', heartbeat=lambda: None, *, scope='all', expert_id=''):
    if scope not in ('all','common','expert') or (scope=='expert') != bool(expert_id):
        raise WikiError('Wiki 갱신 대상을 확인하세요.')
    prompt = load_prompt('compile')
    store = WikiStore(database)
    state = snapshot(database)
    llm = llm or CompanyLLM()
    if scope=='expert':
        if expert_id not in state['experts']:
            raise WikiError('삭제되었거나 사용 중지된 전문가는 갱신할 수 없습니다.')
        catalogue={r['id']:r for r in store.rows()}
        sources={}
        for pid in state['experts'][expert_id]['papers']:
            row=catalogue.get('source-'+pid)
            if not row or not current(row,state,catalogue):
                raise WikiError('담당 논문의 공통 Wiki를 먼저 갱신한 뒤 다시 실행하세요.')
            sources[pid]=row
        shared=[r for r in catalogue.values() if r['scope']=='common' and r['kind']=='topic' and current(r,state,catalogue)]
        # Keep the automatic path's topic order and reuse legacy topics only as
        # explicit note dependencies, so a no-change manual sync creates no revision.
        common=sorted((r for r in shared if r['id']=='common-'+digest(r['title'])[:12]+'-0-0'),key=lambda r:r['title'])
        active=list(sources.values())+shared
        with connect(database) as db:
            requests=[dict(r) for r in db.execute('SELECT * FROM wiki_topic_requests WHERE expert_id=?',(expert_id,))]
        for request in requests:
            if any(key not in {r['id'] for r in active} for key in json.loads(request['source_ids_json'])):
                raise WikiError('특화 노트의 공통 Wiki를 먼저 갱신한 뒤 다시 실행하세요.')
    else:
        active = []; sources = {}
        for paper in state['papers'].values():
            heartbeat()
            generated = source_page(store, state, paper, llm, reason, prompt)
            active += generated
            sources[paper['id']] = generated[-1]
        groups = {}
        for pid, row in sources.items():
            for tag in state['papers'][pid]['tags']:
                groups.setdefault(tag, []).append(row)
        common = []
        for tag, group in sorted(groups.items()):
            parts,root=common_topic(store,state,llm,group,tag,reason,prompt,heartbeat)
            common.append(root); active.extend(parts); active.append(root)
        # Explicit notes may still reference a pre-hierarchy topic ID. Preserve that
        # contract with its original paper set; unreferenced old groups are archived.
        with connect(database) as db:
            requests=[dict(r) for r in db.execute('SELECT * FROM wiki_topic_requests')]
        requested={key for request in requests if request['expert_id'] in state['experts']
                   for key in json.loads(request['source_ids_json'])}
        active_ids={row['id'] for row in active}
        for page_id in sorted(requested-active_ids):
            old=store.record(page_id)
            if not old or old['scope']!='common' or old['kind']!='topic':
                continue
            paper_ids=json.loads(old['refs_json'])['paper_ids']
            if not paper_ids or any(pid not in sources for pid in paper_ids):
                continue
            if not any(request['expert_id'] in state['experts']
                       and page_id in json.loads(request['source_ids_json'])
                       and set(paper_ids).issubset(state['experts'][request['expert_id']]['papers'])
                       for request in requests):
                continue
            heartbeat()
            active.append(synthesize(store,state,llm,[sources[pid] for pid in paper_ids],
                old['id'],old['path'],old['title'],reason=reason,prompt=prompt))
    selected={} if scope=='common' else ({expert_id:state['experts'][expert_id]} if scope=='expert' else state['experts'])
    for eid, expert in selected.items():
        own = [sources[p] for p in expert['papers'] if p in sources]
        # A new expert is a live scoped view over shared knowledge: no repeated
        # paper analysis and no per-expert copy of every common synthesis.
        topics = [r for r in common if set(json.loads(r['refs_json'])['paper_ids']).issubset(expert['papers'])]
        # Specialized notes are created explicitly on demand, and only their own
        # dependencies are refreshed when new evidence changes those inputs.
        for request in (request for request in requests if request['expert_id']==eid):
            dependencies=json.loads(request['source_ids_json'])
            lookup = {r['id']:r for r in active}
            inputs = [lookup[k] for k in dependencies if k in lookup]
            if len(inputs)!=len(dependencies):
                continue
            if any(not set(json.loads(r['refs_json'])['paper_ids']).issubset(expert['papers']) for r in inputs):
                continue
            heartbeat()
            note = synthesize(store,state,llm,inputs,request['id'],f'experts/{eid}/notes/{request["id"]}.md',request['title'],eid,kind='specialization',reason=reason,prompt=prompt)
            topics.append(note); active.append(note)
        heartbeat()
        refs = merge_refs(topics+own, state, eid)
        title = expert['name'] + ' 전문가 Wiki'
        path = f'experts/{eid}/index.md'
        body = '# '+escaped(title)+'\n\n'+escaped(expert['overview'])+'\n\n'
        body += f'담당 논문 {len(own)}편 · 근거 claim {len(refs["claim_ids"])}개\n\n## 연결된 공통 지식과 특화 노트\n\n'
        for row in topics:
            body += f'- [{escaped(row["title"])}]({link_to(path,row["path"])})\n'
        body += '\n## 담당 원천 지식\n\n' + '\n'.join(f'- [{escaped(r["title"])}]({link_to(path,r["path"])})' for r in own)
        if not own:
            body += '\n\n아직 담당 범위에 편입된 지식이 없습니다.'
        elif not topics:
            body += '\n\n담당 원천 지식을 연결했습니다. 필요한 특화 주제는 별도로 구성할 수 있습니다.'
        active.append(store.publish('expert-index-'+eid, path, title, body, refs,
            digest([VERSION, title, expert['hash'], refs, body]), scope='expert', expert_id=eid, kind='expert_index',
            status='ready' if own else 'empty', reason=reason))
    # Only retire pages owned by this job. Other experts keep their Markdown
    # and revision history even when a changed common dependency makes them stale.
    latest = snapshot(database)
    catalogue = {r['id']:r for r in store.rows()}
    valid_ids={r['id'] for r in active if current(r,latest,catalogue)}
    def targeted(row):
        return scope=='all' or (scope=='common' and row['scope']=='common') or (scope=='expert' and row['expert_id']==expert_id)
    with connect(database) as db:
        for r in catalogue.values():
            if targeted(r) and r['id'] not in valid_ids:
                db.execute("UPDATE wiki_files SET status='archived' WHERE id=?", (r['id'],))
                db.execute('DELETE FROM wiki_fts WHERE page_id=?', (r['id'],))
        db.commit()
    catalogue={r['id']:r for r in store.rows()}
    valid=[r for r in catalogue.values() if current(r,latest,catalogue)]
    store.navigation(valid)
    from .wiki_retrieval import reindex
    retrieval=reindex(database,heartbeat,scope=scope,expert_id=expert_id)
    return {'pages':sum(targeted(r) for r in valid), 'scope':scope, 'expert_id':expert_id,
            'llm_configured':llm.configured, 'retrieval':retrieval}


def run_jobs(database=None, llm=None):
    owner = uuid4().hex
    stop_renewal = threading.Event()
    lease_lost = threading.Event()
    acquired = False
    active_trace = None
    from .wiki_usage import WikiJobTrace, recover_runs
    busy_message = 'DB가 사용 중입니다. 잠시 후 Wiki 작업을 자동 재시도합니다.'

    def check_owner(db):
        row = db.execute('SELECT owner FROM wiki_worker_lease WHERE id=1').fetchone()
        if not row or row['owner'] != owner:
            lease_lost.set()
            raise WikiError('Wiki 작업 소유권이 변경되었습니다.')

    def renew():
        if lease_lost.is_set():
            raise WikiError('Wiki 작업 소유권이 변경되었습니다.')
        with write_connection(database) as db:
            check_owner(db)
            db.execute('UPDATE wiki_worker_lease SET expires_at=? WHERE id=1 AND owner=?', (time.time()+LEASE_SECONDS,owner))
            db.execute("UPDATE wiki_job_runs SET heartbeat_at=? WHERE owner=? AND state='running'",(now(),owner))

    def keep_lease():
        # Also renew during a long model request, not only between documents.
        interval = LEASE_RENEW_SECONDS
        while not stop_renewal.wait(interval):
            try:
                renew()
                interval = LEASE_RENEW_SECONDS
            except sqlite3.OperationalError as error:
                if not is_database_busy(error):
                    lease_lost.set()
                    return
                interval = 3
            except WikiError:
                return

    renewal = None
    result = {'status':'idle'}
    errors = []
    try:
        with connect(database) as db:
            if not db.execute("SELECT 1 FROM wiki_jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
                return result
        with write_connection(database) as db:
            lease = db.execute('SELECT * FROM wiki_worker_lease WHERE id=1').fetchone()
            if lease and lease['expires_at'] > time.time():
                return {'status':'busy'}
            db.execute('INSERT OR REPLACE INTO wiki_worker_lease VALUES(1,?,?)', (owner,time.time()+LEASE_SECONDS))
            recover_runs(db)
            db.execute("UPDATE wiki_jobs SET state='queued' WHERE state='running'")
            jobs = [dict(r) for r in db.execute("SELECT id,reason,scope,expert_id FROM wiki_jobs WHERE state='queued' ORDER BY id")]
        acquired = True
        renewal = threading.Thread(target=keep_lease, name='wiki-lease', daemon=True)
        renewal.start()
        for job in jobs:
            renew()
            with write_connection(database) as db:
                check_owner(db)
                db.execute("UPDATE wiki_jobs SET state='running',error='',finished_at=NULL WHERE id=?", (job['id'],))
                active_trace=WikiJobTrace(database,db,job['id'],owner)
            try:
                with active_trace.capture():
                    result = reconcile(database, llm, job['reason'], renew, scope=job['scope'], expert_id=job['expert_id'])
                renew()
                with write_connection(database) as db:
                    check_owner(db)
                    db.execute("UPDATE wiki_jobs SET state='done',finished_at=?,error='' WHERE id=?", (now(),job['id']))
                    active_trace.finish(db,'done')
                active_trace=None
            except (WikiError, LLMError, OSError, ValueError) as error:
                if lease_lost.is_set():
                    return {'status':'deferred','error':'Wiki 작업을 다른 워커가 이어서 처리합니다.'}
                message = str(error) if isinstance(error, (WikiError, LLMError)) else 'Wiki 파일 또는 입력을 확인하세요.'
                with write_connection(database) as db:
                    check_owner(db)
                    db.execute("UPDATE wiki_jobs SET state='failed',finished_at=?,error=? WHERE id=?", (now(),message,job['id']))
                    active_trace.finish(db,'failed')
                active_trace=None
                errors.append(message)
                if isinstance(error, (LLMTimeoutError, LLMCapacityError)):
                    # Other scopes stay queued. Give the slow/shared service a cooldown.
                    return {'status':'failed','error':message,'retry_after_seconds':60}
        return {'status':'failed','error':errors[0]} if errors else result
    except sqlite3.OperationalError as error:
        if not is_database_busy(error):
            raise
        # No failed PDF/Wiki classification for transient writer contention.
        return {'status':'deferred','error':busy_message}
    finally:
        stop_renewal.set()
        if renewal:
            renewal.join(timeout=20)
        if acquired:
            try:
                with write_connection(database) as db:
                    lease = db.execute('SELECT owner FROM wiki_worker_lease WHERE id=1').fetchone()
                    if lease and lease['owner'] == owner:
                        if active_trace:active_trace.finish(db,'interrupted')
                        db.execute("UPDATE wiki_jobs SET state='queued',finished_at=NULL,error=? WHERE state='running'", (busy_message,))
                        db.execute('DELETE FROM wiki_worker_lease WHERE owner=?', (owner,))
            except sqlite3.OperationalError as error:
                if not is_database_busy(error):
                    raise
                # Keep the durable running record; the expired lease recovers it.
                logging.getLogger(__name__).warning('Wiki lease release deferred by database contention; work remains recoverable.')



def scheduled_lint_ready(database):
    # Initial ingest/rebuild takes precedence over optional scheduled model review.
    from .llm_client import model_requests_active
    if model_requests_active():
        return False
    with connect(database) as db:
        return not db.execute("SELECT 1 FROM wiki_jobs WHERE state IN ('queued','running') LIMIT 1").fetchone()


def start_worker(database=None):
    database=Path(database or db_path()).resolve()
    stop = threading.Event()
    def work():
        last_policy_check = 0
        last_prompt_hash = None
        while not stop.is_set():
            pause = 3
            try:
                if time.monotonic()-last_policy_check >= 60:
                    fingerprint = load_prompt('compile').fingerprint
                    if fingerprint != last_prompt_hash:
                        with connect(database) as db:
                            enqueue(db, 'Wiki writing policy checked: '+fingerprint[:12]); db.commit()
                        last_prompt_hash = fingerprint
                    last_policy_check = time.monotonic()
                result = run_jobs(database)
                pause = result.get('retry_after_seconds',3)
            except Exception:
                # The durable outbox/lease makes interrupted jobs recoverable.
                import logging
                logging.getLogger(__name__).exception('Wiki maintenance failed; retrying at next check')
            stop.wait(pause)
    def lint_work():
        from .wiki_lint import run_lint
        while not stop.wait(60):
            try:
                if scheduled_lint_ready(database):
                    run_lint(database,due_only=True)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Wiki lint failed; retrying at next check')
    thread = threading.Thread(target=work, name='wiki-maintenance', daemon=True)
    lint_thread = threading.Thread(target=lint_work,name='wiki-lint',daemon=True)
    thread.start()
    lint_thread.start()
    from .processing import start_ingestion_workers
    ingestion_threads=start_ingestion_workers(stop,database)
    return stop, (thread,lint_thread,*ingestion_threads)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['sync','lint','adopt'])
    parser.add_argument('page_id', nargs='?')
    parser.add_argument('--db', type=Path)
    parser.add_argument('--no-llm', action='store_true', help='Only deterministic link/scope checks (lint)')
    args = parser.parse_args()
    initialize(args.db)
    if args.command == 'sync':
        with connect(args.db) as db:
            enqueue(db, 'manual reconciliation'); db.commit()
        result = run_jobs(args.db)
    else:
        from .wiki_lint import run_lint, adopt
        result = adopt(args.page_id, args.db) if args.command == 'adopt' else run_lint(args.db, use_llm=not args.no_llm)
    print(json.dumps(result, ensure_ascii=True))
    if result.get('status') == 'failed':
        raise SystemExit(1)
