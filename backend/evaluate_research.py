"""Read-only retrieval ablations; never generate answers, reindex, or save traces."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

from .answer_evidence import build_evidence
from .pilot_api import claim_detail
from .pilot_db import db_path
from .wiki_engine import current
from .wiki_retrieval import keywords, rerank
from .wiki_store import WikiStore, digest

VARIANTS = {'raw_only': ('passage',), 'structured': ('passage', 'claim'),
            'with_wiki': ('passage', 'claim', 'wiki')}
LIMITATIONS = [
    '검색·근거 조립 평가이며 과학적 답변 평가가 아닙니다. 답변 모델은 호출하지 않습니다.',
    'Wiki grounded coverage는 원천 의존 근거가 패킷에 함께 있다는 뜻이며 진술의 타당성이나 함의를 검증하지 않습니다.',
    '예산은 직렬화한 문자 수입니다. 모델 token 수나 비용과 같지 않습니다.',
    'reference recall은 지정한 ID의 회수율입니다. ablation별 표현 방식이 다르면 논문 recall과 함께 해석해야 합니다.',
    '단일 실행 지연에는 캐시·실행 순서 영향이 있습니다. 운영 성능은 반복 측정해야 합니다.',
]


@contextmanager
def read_only(database):
    target = Path(database).resolve()
    if not target.is_file():
        raise ValueError('평가할 DB 파일이 없습니다. 먼저 별도 과정에서 자료를 구축하세요.')
    connection = sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        yield connection
    finally:
        connection.close()


def _state(db):
    """Mirror the canonical snapshot's hash shape using only the supplied reader."""
    papers = {}
    for row in db.execute('SELECT * FROM papers ORDER BY id'):
        paper = dict(row)
        paper['claims'] = []
        for claim in db.execute('SELECT * FROM claims WHERE paper_id=? ORDER BY id', (paper['id'],)):
            detail = claim_detail(db, claim)
            detail.pop('experts', None)
            paper['claims'].append(detail)
        paper['documents'] = [dict(r) for r in db.execute('SELECT * FROM paper_documents WHERE paper_id=? ORDER BY id', (paper['id'],))]
        paper['pages'] = [dict(r) for r in db.execute('''SELECT pg.*,d.id AS document_id FROM pages pg
            JOIN paper_documents d ON pg.run_id=d.current_run_id WHERE d.paper_id=? ORDER BY d.id,pg.page_number''', (paper['id'],))]
        notes = db.execute('''SELECT DISTINCT k.id,k.title,k.body,k.limitation FROM knowledge_pages k
            JOIN knowledge_links l ON l.page_id=k.id JOIN claims c ON c.id=l.claim_id
            WHERE c.paper_id=? ORDER BY k.id''', (paper['id'],)).fetchall()
        paper['notes'] = [dict(n) for n in notes if db.execute('''SELECT count(DISTINCT c.paper_id)
            FROM knowledge_links l JOIN claims c ON c.id=l.claim_id WHERE l.page_id=?''', (n['id'],)).fetchone()[0] == 1]
        paper['tags'] = [r[0] for r in db.execute('SELECT tag FROM paper_topics WHERE paper_id=? ORDER BY tag', (paper['id'],))] or ['Other']
        paper['hash'] = digest(paper)
        papers[paper['id']] = paper
    experts = {}
    for row in db.execute('''SELECT * FROM expert_categories WHERE id NOT IN (SELECT expert_id FROM expert_deletions)
        AND id NOT IN (SELECT expert_id FROM expert_policies WHERE enabled=0) ORDER BY id'''):
        expert = dict(row)
        expert['papers'] = [r[0] for r in db.execute('SELECT paper_id FROM expert_papers WHERE expert_id=? ORDER BY paper_id', (expert['id'],))]
        policy = db.execute('SELECT policy_json FROM expert_policies WHERE expert_id=?', (expert['id'],)).fetchone()
        expert['policy'] = policy[0] if policy else ''
        expert['hash'] = digest(expert)
        experts[expert['id']] = expert
    return {'papers': papers, 'experts': experts}


def _fingerprint(db):
    tables = {
        'corpus': 'SELECT revision FROM wiki_corpus_clock WHERE id=1',
        'experts': 'SELECT * FROM expert_categories ORDER BY id',
        'memberships': 'SELECT * FROM expert_papers ORDER BY expert_id,paper_id',
        'policies': 'SELECT * FROM expert_policies ORDER BY expert_id',
        'deleted': 'SELECT * FROM expert_deletions ORDER BY expert_id',
        'wikis': 'SELECT id,content_hash,status FROM wiki_files ORDER BY id',
        'index': 'SELECT id,input_hash,model_fingerprint FROM retrieval_units ORDER BY id',
    }
    return digest({key: [list(r) for r in db.execute(sql)] for key, sql in tables.items()})


def _strings(value, name, *, nonempty=False):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(name + '는 비어 있지 않은 문자열의 배열이어야 합니다.')
    result = list(dict.fromkeys(value))
    if nonempty and not result:
        raise ValueError(name + '에 최소 한 항목이 필요합니다.')
    return result


def validate_dataset(dataset):
    if not isinstance(dataset, dict) or not isinstance(dataset.get('cases'), list) or not dataset['cases']:
        raise ValueError('cases 배열이 있는 평가 JSON이 필요합니다.')
    ids = set()
    for case in dataset['cases']:
        if not isinstance(case, dict) or not isinstance(case.get('id'), str) or not case['id'].strip():
            raise ValueError('각 case에는 문자열 id가 필요합니다.')
        if case['id'] in ids:
            raise ValueError('중복 case id: ' + case['id'])
        ids.add(case['id'])
        if not isinstance(case.get('question'), str) or not 1 <= len(case['question'].strip()) <= 1000:
            raise ValueError('각 question은 1~1000자여야 합니다.')
        _strings(case.get('expert_ids'), 'expert_ids', nonempty=True)
        if 'expected_paper_ids' in case:
            _strings(case['expected_paper_ids'], 'expected_paper_ids')
        expected = case.get('expected_reference_ids', [])
        if isinstance(expected, dict):
            if any(key not in VARIANTS for key in expected):
                raise ValueError('expected_reference_ids의 ablation 이름이 올바르지 않습니다.')
            values = expected.values()
        else:
            values = [expected]
        for value in values:
            for reference in _strings(value, 'expected_reference_ids'):
                if reference.split(':', 1)[0] not in ('passage', 'claim', 'wiki') or ':' not in reference:
                    raise ValueError('reference ID는 passage:, claim:, wiki: 유형 접두사가 필요합니다.')
    return dataset


def _expected_references(case, variant):
    expected = case.get('expected_reference_ids', [])
    return set(expected.get(variant, []) if isinstance(expected, dict) else expected)


def _scope(expert_ids):
    marks = ','.join('?' for _ in expert_ids)
    return f'''(u.expert_id IS NULL OR (u.expert_id IN ({marks})
        AND u.expert_id NOT IN (SELECT expert_id FROM expert_deletions)
        AND u.expert_id NOT IN (SELECT expert_id FROM expert_policies WHERE enabled=0))) AND NOT EXISTS (
        SELECT 1 FROM json_each(u.paper_ids_json) p WHERE p.value NOT IN (
        SELECT ep.paper_id FROM expert_papers ep WHERE ep.expert_id IN ({marks})
        AND ep.expert_id NOT IN (SELECT expert_id FROM expert_deletions)
        AND ep.expert_id NOT IN (SELECT expert_id FROM expert_policies WHERE enabled=0)))
        AND (u.kind<>'wiki' OR EXISTS (SELECT 1 FROM wiki_files w WHERE w.id=u.reference_id
        AND w.status NOT IN ('archived','needs_source','empty')
        AND w.kind IN ('source','topic','specialization')))''', tuple(expert_ids) * 2


def _model_context(allow_models):
    if not allow_models:
        return None, None
    # Import/configure only after explicit opt-in. No answer LLM is constructed.
    from .chunking import load_chunk_policy
    from .ai_settings import load_ai_settings
    return load_chunk_policy(), load_ai_settings().reranker


def _query_vector(question, policy):
    if not policy or not policy.embedding:
        return None, None, 0, []
    if len(policy.splitter(policy.embedding.max_input_tokens - 32).chunks(question)) != 1:
        return None, None, 0, ['긴 질문의 embedding은 생략했습니다.']
    from .embedding_client import EmbeddingClient, EmbeddingError
    client = EmbeddingClient(policy.embedding)
    try:
        # Do not use CachedEmbedder: the corpus and its query cache are read-only.
        vector = client.embed([question])[0]
        return vector, digest(policy.embedding.identity()), client.requests, []
    except (EmbeddingError, ValueError, OSError):
        return None, None, client.requests, ['Embedding 요청을 완료하지 못해 키워드 검색으로 평가했습니다.']


def _retrieve(db, question, expert_ids, kinds, limit, vector, model_fingerprint, reranker):
    scope, args = _scope(expert_ids)
    kind_marks = ','.join('?' for _ in kinds)
    scope += f' AND u.kind IN ({kind_marks})'
    args += kinds
    terms = keywords(question)
    lexical, semantic, notices = [], [], []
    if terms:
        expression = ' OR '.join('"' + term.replace('"', '""') + '"' for term in terms)
        lexical = [dict(r) for r in db.execute('''SELECT u.*,bm25(retrieval_fts,0,2,1) AS score
            FROM retrieval_fts JOIN retrieval_units u ON u.id=retrieval_fts.unit_id
            WHERE retrieval_fts MATCH ? AND ''' + scope + ' ORDER BY score,u.id LIMIT 60', (expression, *args))]
    if vector is not None:
        scored = []
        for row in db.execute('SELECT u.* FROM retrieval_units u WHERE ' + scope + ' AND model_fingerprint=?', (*args, model_fingerprint)):
            stored = json.loads(row['vector_json']) if row['vector_json'] else []
            if len(stored) != len(vector):
                notices.append('차원이 다른 vector를 평가에서 제외했습니다.')
                continue
            scored.append((sum(a * b for a, b in zip(vector, stored)), dict(row)))
        semantic = [row for _, row in sorted(scored, key=lambda item: (-item[0], item[1]['id']))[:60]]
        if not semantic:
            notices.append('선택된 범위와 유형에 해당 모델의 vector 색인이 없습니다.')
    units, scores = {}, {}
    for ranking in (lexical, semantic):
        for rank, unit in enumerate(ranking):
            units[unit['id']] = unit
            scores[unit['id']] = scores.get(unit['id'], 0) + 1 / (61 + rank)
    ranked = [units[key] for key in sorted(units, key=lambda key: (-scores[key], key))[:limit]]
    calls = 0
    if ranked and reranker and reranker.base_url and reranker.model and reranker.api_key.get_secret_value():
        calls = 1
        try:
            ranked = rerank(question, ranked, reranker)
        except ValueError:
            notices.append('Reranker 요청 실패: 결합 검색 순위를 사용했습니다.')
    return ranked, list(dict.fromkeys(notices)), calls


def _recall(expected, actual):
    return len(expected.intersection(actual)) / len(expected) if expected else None


def evaluate(dataset, database=None, *, budget=32000, top_k=30, allow_models=False):
    validate_dataset(dataset)
    if type(budget) is not int or not 2 <= budget <= 200000 or type(top_k) is not int or not 1 <= top_k <= 60:
        raise ValueError('budget은 2~200000자, top_k는 1~60이어야 합니다.')
    database = Path(database or db_path()).resolve()
    started = time.perf_counter()
    report = dict(version=1, dataset_type=dataset.get('dataset_type', 'unspecified'),
        evaluation='retrieval_and_evidence_only', allow_models=allow_models,
        settings=dict(budget_chars=budget, top_k=top_k, variants=list(VARIANTS)),
        cases=[], failures=[], limitations=list(LIMITATIONS), model_requests=dict(embedding=0, reranker=0, answer=0))
    if report['dataset_type'] == 'test-demo':
        report['limitations'].append('test-demo 합성 예제입니다. 실제 논문에 대한 전문가 정답이나 품질 인증이 아닙니다.')
    if not allow_models:
        report['limitations'].append('기본 오프라인 평가: embedding과 reranker를 호출하지 않고 키워드 검색만 비교했습니다.')
    policy, reranker = _model_context(allow_models)
    with read_only(database) as db:
        report['corpus_fingerprint'] = _fingerprint(db)
        state = _state(db)
        store = WikiStore(database)
        all_pages = {r['id']: dict(r) for r in db.execute('SELECT * FROM wiki_files ORDER BY id')}
        catalogue = {key: row for key, row in all_pages.items() if row['status'] != 'archived' and current(row, state, all_pages)}
        known_refs = {r['kind'] + ':' + r['reference_id'] for r in db.execute('SELECT DISTINCT kind,reference_id FROM retrieval_units')}
        known_refs.update('claim:' + c['id'] for paper in state['papers'].values() for c in paper['claims'])
        for case in dataset['cases']:
            ids = list(dict.fromkeys(case['expert_ids']))
            expected_papers = set(case.get('expected_paper_ids', []))
            errors = []
            if any(eid not in state['experts'] for eid in ids):
                errors.append('unknown_or_inactive_expert')
            if expected_papers - state['papers'].keys():
                errors.append('unknown_expected_paper')
            if any(_expected_references(case, variant) - known_refs for variant in VARIANTS):
                errors.append('unknown_expected_reference')
            entry = dict(id=case['id'], question=case['question'], expert_ids=ids, variants={})
            report['cases'].append(entry)
            if errors:
                entry['errors'] = errors
                report['failures'].append(dict(case_id=case['id'], kind='invalid_case', reasons=errors))
                continue
            vector_started = time.perf_counter()
            vector, identity, calls, shared_notices = _query_vector(case['question'], policy)
            entry['shared_embedding_ms'] = round((time.perf_counter() - vector_started) * 1000, 3)
            report['model_requests']['embedding'] += calls
            for variant, kinds in VARIANTS.items():
                variant_started = time.perf_counter()
                ranked, notices, calls = _retrieve(db, case['question'], ids, kinds, top_k, vector, identity, reranker)
                report['model_requests']['reranker'] += calls
                retrieved_at = time.perf_counter()
                bundle = build_evidence(ranked, ids, store=store, state=state, catalogue=catalogue, budget=budget, database=database)
                evidence, diagnostics = bundle['evidence'], bundle['diagnostics']
                selected_units = set(diagnostics['selected_units'])
                selected_refs = {unit['kind'] + ':' + unit['reference_id'] for unit in ranked if unit['id'] in selected_units}
                selected_refs.update('claim:' + ev['claim']['id'] for ev in evidence.values() if ev['kind'] == 'claim')
                candidate_papers = {pid for unit in ranked for pid in json.loads(unit['paper_ids_json'])}
                selected_papers = set(diagnostics['paper_ids'])
                expected_refs = _expected_references(case, variant)
                wikis = [ev for ev in evidence.values() if ev['kind'] == 'wiki']
                supported = [ev for ev in wikis if ev.get('support_ids') and all(key in evidence and evidence[key]['kind'] != 'wiki' for key in ev['support_ids'])]
                metrics = dict(candidate_count=len(ranked), selected_unit_count=len(selected_units),
                    candidate_paper_recall=_recall(expected_papers, candidate_papers),
                    selected_paper_recall=_recall(expected_papers, selected_papers),
                    selected_reference_recall=_recall(expected_refs, selected_refs),
                    selected_paper_diversity=len(selected_papers), budget_chars=budget,
                    evidence_chars=diagnostics['used_chars'], budget_utilization=diagnostics['used_chars'] / budget,
                    selected_wiki_count=len(wikis), supported_wiki_count=len(supported),
                    grounded_wiki_coverage=len(supported) / len(wikis) if wikis else None,
                    retrieval_ms=round((retrieved_at - variant_started) * 1000, 3),
                    total_ms=round((time.perf_counter() - variant_started) * 1000, 3))
                missing_papers = sorted(expected_papers - selected_papers)
                missing_refs = sorted(expected_refs - selected_refs)
                entry['variants'][variant] = dict(metrics=metrics, selected_paper_ids=sorted(selected_papers),
                    selected_reference_ids=sorted(selected_refs), missing_paper_ids=missing_papers,
                    missing_reference_ids=missing_refs, dropped_units=diagnostics['dropped_units'],
                    notices=list(dict.fromkeys(shared_notices + notices + diagnostics['notices'])))
                if missing_papers or missing_refs:
                    report['failures'].append(dict(case_id=case['id'], variant=variant, kind='expectation_miss',
                        missing_paper_ids=missing_papers, missing_reference_ids=missing_refs))
    with read_only(database) as db:
        report['corpus_unchanged'] = report['corpus_fingerprint'] == _fingerprint(db)
    if not report['corpus_unchanged']:
        report['failures'].append(dict(kind='corpus_changed_during_evaluation'))
        report['limitations'].append('평가 중 corpus가 변경되어 ablation 간 비교를 유효하다고 볼 수 없습니다. 다시 실행하세요.')
    report['summary'] = {}
    for variant in VARIANTS:
        metrics = [case['variants'][variant]['metrics'] for case in report['cases'] if variant in case['variants']]
        report['summary'][variant] = {'evaluated_cases': len(metrics)}
        for key in ('selected_paper_recall', 'selected_reference_recall', 'selected_paper_diversity', 'evidence_chars', 'grounded_wiki_coverage', 'total_ms'):
            values = [item[key] for item in metrics if item[key] is not None]
            report['summary'][variant][key] = sum(values) / len(values) if values else None
    report['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 3)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--budget', type=int, default=32000)
    parser.add_argument('--top-k', type=int, default=30)
    parser.add_argument('--allow-models', action='store_true', help='Allow configured embedding/reranker requests; never call the answer LLM')
    parser.add_argument('--output', type=Path, help='Write a report instead of printing JSON; does not change the corpus')
    args = parser.parse_args(argv)
    try:
        dataset = json.loads(args.cases.read_text(encoding='utf-8'))
        report = evaluate(dataset, args.database, budget=args.budget, top_k=args.top_k, allow_models=args.allow_models)
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            if args.output.resolve() in {args.cases.resolve(), Path(args.database or db_path()).resolve()}:
                raise ValueError('평가 입력 또는 DB 파일을 결과로 덮어쓸 수 없습니다.')
            args.output.write_text(text + '\n', encoding='utf-8')
        else:
            print(text)
        return 1 if report['failures'] else 0
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
