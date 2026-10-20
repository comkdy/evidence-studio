"""Shared evidence, scoped expert perspectives, and bounded research rounds."""
import json
import time
from uuid import uuid4
from fastapi import HTTPException
from .wiki_store import WikiError, digest
from .llm_client import CompanyLLM, LLMError, LLMTimeoutError, LLMCapacityError
from .wiki_retrieval import retrieve, keywords
from .wiki_policy import load_prompt
from .answer_evidence import build_evidence
from .answer_validation import grounded_sections, render_answer, review_items, apply_review
from .answer_trace import save_run
from .conversation_context import request_context, contextual_query, context_fields, has_private_context
from .research_progress import checkpoint as research_checkpoint, stage
from .ai_settings import load_ai_settings

VERSION = 'research-answer-v1'
MAX_QUERIES = 6
MAX_MODEL_CALLS = 4
MAX_INPUT_CHARS = 100000
COMPANY_LLM_TYPE = CompanyLLM


def _queries(value, limit):
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(q, str) or not 1 <= len(q.strip()) <= 240 for q in value):
        raise WikiError('추가 검색 계획의 형식을 확인해야 합니다.')
    return list(dict.fromkeys(q.strip() for q in value))


def _gaps(value):
    if not isinstance(value, list) or len(value) > 5 or any(not isinstance(q, str) or len(q) > 240 for q in value):
        raise WikiError('조사 한계의 형식을 확인해야 합니다.')
    return [q for q in value if q.strip()]


def _merge(results):
    # RRF across research questions, duplicate units counted once per query.
    scores, units = {}, {}
    for rows in results:
        seen = set()
        for rank, row in enumerate(rows):
            key = row['id']
            if key in seen:
                continue
            seen.add(key)
            units[key] = row
            scores[key] = scores.get(key, 0) + 1 / (60 + rank + 1)
    return [units[k] for k in sorted(units, key=lambda k: (-scores[k], k))]


def _profiles(ids, state, evidence):
    profiles = []
    for eid in ids:
        expert = state['experts'][eid]
        own = set(expert['papers'])
        allowed = [key for key, ev in evidence.items()
                   if set(ev['paper_ids']).issubset(own) and ev.get('expert_id') in (None, eid)]
        profiles.append(dict(expert_id=eid, expert_name=expert['name'],
                             overview=expert['overview'][:1000], evidence_ids=allowed))
    return profiles


def _limitations(evidence, state):
    papers = {pid for ev in evidence.values() for pid in ev['paper_ids']}
    notes = []
    if not papers:
        return ['선택한 전문가의 담당 범위에서 답변에 사용할 원천 근거를 찾지 못했습니다.']
    if len(papers) == 1:
        notes.append('한 편의 논문에서 확보한 근거입니다. 여러 연구의 합의로 해석할 수 없습니다.')
    claims = [ev['claim'] for ev in evidence.values() if ev['kind'] == 'claim']
    if any(c.get('review_status') != 'approved' for c in claims):
        notes.append('선별 claim에 사람 검토 대기 근거가 포함되어 있습니다. 원문 위치 확인은 과학적 검증과 다릅니다.')
    if any(any(c.get('raw_value') is None for c in m.get('conditions', []))
           for claim in claims for m in claim.get('measurements', [])):
        notes.append('일부 측정 조건이 미기재입니다. 같은 조건의 직접 비교로 단정할 수 없습니다.')
    if any(any(p['status'] != 'ready' for p in state['papers'][pid]['pages']) for pid in papers):
        notes.append('선택된 논문에 원문 추출·OCR 확인이 필요한 페이지가 있습니다.')
    notes.append('검색으로 선택한 근거를 검토한 결과이며, 관련 문헌 전체를 빠짐없이 검토했다는 의미는 아닙니다.')
    return notes


def answer(request, llm=None, *, database=None, prepared=None, context=None):
    from .wiki_api import load_catalogue, read_page
    from .terminology import snapshot as term_snapshot, prompt_rules, prompt_text, query_rules
    def checkpoint(phase=None):
        research_checkpoint()
        if context:
            context.check_cancelled()
            if phase:
                context.checkpoint({**getattr(context,'checkpoint_data',{}),'phase':phase})
    checkpoint()
    conversation = request_context(request,database)
    stage('context', '앞선 대화의 주제와 질문 범위를 확인했습니다.')
    start = time.monotonic()
    max_seconds = load_ai_settings().limits.research_timeout_seconds
    deadline = start + max_seconds
    store, state, catalogue = load_catalogue(database)
    glossary=term_snapshot(store.database)
    llm = llm or CompanyLLM(role='answer',database=database)
    review_llm = CompanyLLM(role='review',database=database) if isinstance(llm,COMPANY_LLM_TYPE) else llm
    ids = list(dict.fromkeys(request.expert_ids))
    if any(eid not in state['experts'] for eid in ids):
        raise HTTPException(409, '삭제되었거나 사용 중지된 전문가입니다. 다시 선택하세요.')
    if prepared:
        from .answer_cache import validity,scope_key
        if (has_private_context(request) or prepared.get('question')!=request.question.strip()
            or prepared.get('scope_key')!=scope_key(ids)
            or not prepared.get('bundle',{}).get('investigation_complete')
            or prepared.get('validity_hash')!=validity(ids,database=database)):
            prepared=None
    run_id = str(uuid4())
    mode = 'deep'
    budget = 48000
    trace = dict(version=VERSION, searches=[], model_calls=[], prompt_hashes={},
                 terminology_revision=glossary['revision'],
                 limits=dict(max_queries=MAX_QUERIES, max_model_calls=MAX_MODEL_CALLS,
                             evidence_chars=budget, scheduling_seconds=max_seconds))
    notices, prompts, queries, results = [], {}, [], []
    groups, raw_groups, validation = {}, {}, 'not_generated'
    model_available = True
    progress={}
    if context:
        from .answer_cache import validity
        resume_key=digest([request.question,sorted(ids),context_fields(request),conversation,
                          validity(ids,database=database,model_identity=llm.identity),review_llm.identity])
        prior=getattr(context,'checkpoint_data',{})
        progress=dict(prior.get('answer_outputs',{})) if prior.get('answer_resume_key')==resume_key else {}
        context.checkpoint({**prior,'answer_resume_key':resume_key,'answer_outputs':progress})

    try:
        for operation in ('answer', 'plan', 'refine', 'review'):
            prompts[operation] = load_prompt(operation)
            trace['prompt_hashes'][operation] = prompts[operation].fingerprint
        if len({p.policy_hash for p in prompts.values()}) != 1:
            raise WikiError('지침이 갱신 중입니다. 다시 질문해 주세요.')
    except WikiError as error:
        notices.append(str(error))
        prompts = {}

    def call(operation, payload):
        nonlocal model_available
        checkpoint()
        labels = {'plan':'추가로 확인할 논문과 검색 방향을 정리하고 있습니다.',
                  'refine':'찾은 근거와 빠진 조건을 검토하고 있습니다.',
                  'answer':'논문 근거를 바탕으로 답변을 작성하고 있습니다.',
                  'review':'답변의 주장·수치·인용을 다시 확인하고 있습니다.'}
        stage(operation, labels[operation])
        checkpoint(operation)
        payload = dict(payload, conversation_context=conversation)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or len(trace['model_calls']) >= MAX_MODEL_CALLS:
            raise WikiError('심층 분석 시간 또는 모델 호출 한도에 도달했습니다.')
        rules=prompt_rules(json.dumps(payload,ensure_ascii=False),glossary)
        system=prompt_text(prompts[operation].text,rules)
        chars = len(json.dumps(payload, ensure_ascii=False)) + len(system)
        if chars > MAX_INPUT_CHARS:
            raise WikiError('분석 입력 한도에 도달했습니다. 질문 범위를 좁혀 주세요.')
        entry = dict(operation=operation, input_chars=chars, status='started',terminology=rules)
        trace['model_calls'].append(entry)
        called_at = time.monotonic()
        try:
            payload_hash=digest([system,payload])
            cached=progress.get(operation)
            client=review_llm if operation=='review' else llm
            if cached and cached.get('input_hash')==payload_hash:
                result=cached['result'];entry['status']='resumed_checkpoint'
            else:
                result = (client.complete_json(system, payload, deadline=deadline) if isinstance(client, COMPANY_LLM_TYPE)
                          else client.complete_json(system, payload))
            checkpoint()
            if not isinstance(result, dict):
                raise WikiError('모델 응답 형식을 확인해야 합니다.')
            if entry['status']!='resumed_checkpoint':entry['status'] = 'completed'
            if context:
                progress[operation]={'input_hash':payload_hash,'result':result}
                context.checkpoint({**context.checkpoint_data,'answer_outputs':progress})
            checkpoint(operation+'_completed')
            return result
        except (WikiError, LLMError) as error:
            entry['status'] = 'failed'
            if isinstance(error, (LLMTimeoutError, LLMCapacityError)):
                model_available = False  # Do not compound provider overload with later phases.
            raise
        finally:
            entry['elapsed_ms'] = round((time.monotonic()-called_at)*1000)

    def search(query):
        checkpoint()
        if query.casefold() in {q.casefold() for q in queries}:
            return
        if len(queries) >= MAX_QUERIES or time.monotonic() >= deadline:
            notices.append('조사 예산에 도달하여 추가 검색을 종료했습니다.')
            return
        stage('search', '전문가의 Wiki와 담당 논문에서 관련 근거를 검색하고 있습니다.')
        queries.append(query)
        ranked, messages = retrieve(query, ids, database=database,limit=60,glossary=glossary)
        checkpoint()
        results.append(ranked)
        notices.extend(messages)
        trace['searches'].append(dict(query=query, terminology=query_rules(query,glossary), candidates=[dict(id=r['id'], kind=r['kind'],
            reference_id=r['reference_id'], paper_ids=json.loads(r['paper_ids_json']),
            input_hash=r['input_hash']) for r in ranked]))

    if prepared:
        queries.extend(prepared['queries'])
        trace['plan']={'queries':prepared['queries'],'gaps':[]}
        trace['refinement']={'queries':[],'gaps':prepared['bundle'].get('investigation_gaps',[])}
        trace['prepared_case']={'id':prepared['id'],'validity_hash':prepared['validity_hash']}
        trace['prepared_case_id']=prepared['id']
    else:
        search(request.question)
        if conversation:
            search(contextual_query(request.question, conversation))
    if llm.configured and prompts and model_available and not prepared:
        try:
            plan = call('plan', dict(question=request.question, experts=_profiles(ids, state, {})))
            trace['plan'] = dict(queries=_queries(plan.get('queries'), 3), gaps=_gaps(plan.get('gaps', [])))
            for query in trace['plan']['queries']:
                search(query)
        except (LLMError, WikiError) as error:
            notices.append(str(error))

    def assemble():
        stage('evidence', '질문과 연결된 주장·수치·실험 조건을 모으고 있습니다.')
        return build_evidence(_merge(results), ids, store=store, state=state,
                              catalogue=catalogue, budget=budget,database=database)

    bundle = prepared['bundle'] if prepared else assemble()
    if llm.configured and prompts and model_available and bundle['evidence'] and not prepared:
        try:
            refined = call('refine', dict(question=request.question, queries=queries,
                experts=_profiles(ids, state, {}), evidence=bundle['evidence']))
            followups = _queries(refined.get('queries'), 2)
            trace['refinement'] = dict(queries=followups, gaps=_gaps(refined.get('gaps', [])))
            for query in followups:
                search(query)
            if followups:
                bundle = assemble()
        except (LLMError, WikiError) as error:
            notices.append(str(error))

    evidence = bundle['evidence']
    notices.extend(bundle['diagnostics'].get('notices', []))
    profiles = _profiles(ids, state, evidence)
    limitations = _limitations(evidence, state)
    # Refinement gaps predate the final retrieval and are not automatically still
    # missing. The answer must reassess them against the final evidence.
    if llm.configured and prompts and model_available and evidence:
        try:
            result = call('answer', dict(question=request.question, mode=mode, experts=profiles,
                evidence=evidence, limitations=limitations,
                unverified_feedback=prepared['bundle'].get('unverified_feedback',[]) if prepared else [],
                investigation_gaps=trace.get('refinement', trace.get('plan', {})).get('gaps', [])))
            if len(json.dumps(result, ensure_ascii=False)) > 24000:
                raise WikiError('답변이 검증 가능한 길이를 초과했습니다.')
            trace['generated_response'] = result
            contributions = result.get('answers')
            if not isinstance(contributions, list) or len(contributions) != len(ids) or any(not isinstance(a, dict) or not isinstance(a.get('expert_id'), str) or a.get('expert_id') not in ids for a in contributions) or len({a['expert_id'] for a in contributions}) != len(ids):
                raise WikiError('전문가별 답변 형식이 올바르지 않습니다.')
            groups['synthesis'] = grounded_sections(result.get('synthesis', {}), evidence)
            for a in contributions:
                allowed = next(p['evidence_ids'] for p in profiles if p['expert_id'] == a['expert_id'])
                groups['expert-'+a['expert_id']] = grounded_sections(a, {k: evidence[k] for k in allowed})
            raw_groups = json.loads(json.dumps(groups, ensure_ascii=False))
            validation = 'provenance_checked'
            if review_items(groups):
                review = call('review', dict(question=request.question, evidence=evidence,
                                            statements=review_items(groups)))
                trace['review'] = review
                rejected = apply_review(groups, review)
                validation = 'model_review_partial' if rejected else 'model_reviewed'
                if rejected:
                    notices.append('일부 결론이 자동 근거 검토를 통과하지 못해 해당 답변을 보류했습니다.')
        except (LLMError, WikiError) as error:
            trace['validation_error'] = str(error)
            notices.append(str(error))
            groups = {}
            validation = 'withheld'
    if not llm.configured:
        notices.append('사내 LLM 연결 전이므로 추가 조사와 내용 검토를 수행하지 않았습니다.')
    if validation.startswith('model_review'):
        limitations.append('자동 내용 검토를 수행했으며, 사람의 과학적 검증을 대체하지 않습니다.')

    def result_part(key, allowed, expert=None):
        sections = groups.get(key, [])
        used = {eid for section in sections for st in section['statements'] for eid in st['evidence_ids']}
        wiki_ids = {evidence[eid]['id'] for eid in used if evidence[eid]['kind'] == 'wiki'}
        if sections:
            body = render_answer(sections, evidence, run_id=run_id)
        elif not allowed:
            body = '담당 범위에서 질문에 답할 원천 근거를 찾지 못했습니다.'
        elif not llm.configured:
            body = '사내 LLM 연결 전입니다. 검색한 논문 근거와 조건을 확인해 주세요.'
        else:
            body = '확인된 근거만으로 검증을 통과한 결론을 제공하지 못했습니다. 검색한 근거를 먼저 보여드립니다.'
        part = dict(markdown=body, mode='llm' if sections else 'evidence',
                    notice=' '.join(dict.fromkeys(notices)),
                    wikis=[w for w in bundle['wikis'] if w['id'] in wiki_ids])
        if expert:
            part.update(expert_id=expert['expert_id'], expert_name=expert['expert_name'])
        return part

    synthesis = result_part('synthesis', evidence)
    answers = [result_part('expert-'+p['expert_id'], p['evidence_ids'], p) for p in profiles]
    claims = [ev['claim'] for ev in evidence.values() if ev['kind'] == 'claim']
    hits = [dict(id=key, paper_id=ev['paper_id'], title=state['papers'][ev['paper_id']]['title'],
                 text=ev['text'], page_number=ev['page_number'], page_id=ev['page_id'], document_id=ev['document_id'])
            for key, ev in evidence.items() if ev['kind'] == 'source_text']
    paper_ids = sorted({pid for ev in evidence.values() for pid in ev['paper_ids']})
    result = dict(claims=claims, total_claims=len(claims), hits=hits, terms=keywords(request.question,database),
        note='선택한 전문가들의 담당 범위에서 찾은 원천 근거입니다. 검색 근거 목록과 실제 답변 인용은 구분됩니다.',
        answers=answers, synthesis=synthesis, run_id=run_id,
        research=dict(mode=mode, queries=queries, rounds=len(queries), paper_count=len(paper_ids),
                      validation_status=validation, limitations=list(dict.fromkeys(limitations+notices))))
    trace.update(diagnostics=bundle['diagnostics'], experts=profiles,
                 expert_hashes={eid: state['experts'][eid]['hash'] for eid in ids},
                 paper_hashes={pid: state['papers'][pid]['hash'] for pid in paper_ids},
                 wikis=bundle['wikis'], generated_sections=raw_groups,
                 published_sections=groups, usage=getattr(llm, 'usage_history', [])+
                   (getattr(review_llm,'usage_history',[]) if review_llm is not llm else []),
                 model_timings=getattr(llm, 'call_history', [])+
                   (getattr(review_llm,'call_history',[]) if review_llm is not llm else []))
    stage('verify', '근거의 최신 상태를 확인하고 답변을 정리하고 있습니다.')
    checkpoint('publishing')
    _, latest, valid = load_catalogue(database)
    stale = any(eid not in latest['experts'] or latest['experts'][eid]['hash'] != state['experts'][eid]['hash'] for eid in ids)
    stale |= any(pid not in latest['papers'] or latest['papers'][pid]['hash'] != state['papers'][pid]['hash'] for pid in paper_ids)
    if prepared:
        from .answer_cache import validity
        stale |= prepared['validity_hash'] != validity(ids,database=database)
    try:
        for wiki in bundle['wikis']:
            row = valid.get(wiki['id'])
            if not row or row['revision'] != wiki['revision']:
                stale = True
            else:
                read_page(row, store, valid)
    except WikiError:
        stale = True
    checkpoint()
    request_context(request,database)  # Owner/current report revision still applies at publication.
    trace.update(context_fields(request))
    if stale:
        save_run(run_id, request, model_identity=llm.identity, prompt=prompts.get('answer'),
            evidence=evidence, trace=trace, result={'error': 'evidence_changed'},
            elapsed_ms=int((time.monotonic()-start)*1000), status='superseded',database=database)
        raise HTTPException(409, '답변 준비 중 지식 또는 전문가 설정이 갱신되었습니다. 다시 질문해 주세요.')
    save_run(run_id, request, model_identity=llm.identity, prompt=prompts.get('answer'),
        evidence=evidence, trace=trace, result=result,
        elapsed_ms=int((time.monotonic()-start)*1000), status='completed' if synthesis['mode'] == 'llm' else 'evidence_only',database=database)
    return result
