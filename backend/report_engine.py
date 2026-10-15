"""One-section report drafting from immutable, owner-scoped evidence snapshots."""
import copy
import hashlib
import json
import time
from fastapi import HTTPException
from .answer_trace import read_run
from .answer_validation import grounded_sections
from .llm_client import CompanyLLM, LLMError
from .report_store import read_report, section_in, check_revision, save_report
from .research_progress import checkpoint, stage
from .wiki_budget import budget_for
from .wiki_policy import load_prompt
from .wiki_store import WikiError


def evidence_from_runs(run_ids):
    evidence = {}
    for run_id in reversed(list(dict.fromkeys(run_ids))):
        run = read_run(run_id)
        if run['status'] not in ('completed', 'evidence_only'):
            raise HTTPException(409, '완료된 답변의 근거만 보고서에 사용할 수 있습니다.')
        # Prefer evidence actually cited in the published answer, then retrieved evidence.
        cited = [key for group in run['trace'].get('published_sections', {}).values()
                 for section in group for statement in section['statements'] for key in statement['evidence_ids']]
        titles = {h['paper_id']: h['title'] for h in run['result'].get('hits', [])}
        titles.update({c['paper_id']: c['title'] for c in run['result'].get('claims', []) if c.get('title')})
        for key in dict.fromkeys(cited + list(run['evidence'])):
            item = run['evidence'].get(key, {})
            if item.get('kind') not in ('claim', 'source_text'):
                continue
            ev = copy.deepcopy(item)
            paper_id = ev.get('paper_id') or ev.get('claim', {}).get('paper_id')
            if paper_id and not titles.get(paper_id):
                from .pilot_db import connect
                with connect() as db:
                    row = db.execute('SELECT title FROM papers WHERE id=?', (paper_id,)).fetchone()
                titles[paper_id] = row['title'] if row else paper_id
            ev.update(answer_run_id=run_id, original_key=key, paper_title=titles.get(paper_id, paper_id or '논문'))
            evidence['ref-'+hashlib.sha256((run_id+':'+key).encode()).hexdigest()[:24]] = ev
    return evidence


def edit_section(report_id, request, llm=None):
    report = read_report(report_id)
    check_revision(report, request.revision)
    section = section_in(report, request.section_id)
    llm = llm or CompanyLLM()
    if not llm.configured:
        raise HTTPException(503, '보고서 작성을 위해 사내 LLM 주소·모델·API key를 설정해 주세요.')
    start = time.monotonic()
    stage('report_context', '선택한 구간과 기존 인용 근거를 확인하고 있습니다.')
    candidates = evidence_from_runs(request.run_ids)
    research_run_id = None
    if request.research:
        from .wiki_api import Question, recommendations, answer
        stage('report_search', '선택한 구간에 필요한 추가 논문 근거를 찾고 있습니다.')
        question = (report['document']['title']+' / '+section['title']+'\n'+request.instruction)[:1000]
        ids = request.expert_ids
        if request.selection == 'auto':
            ids = [e['id'] for e in recommendations(question)['experts'][:2]]
        if not ids:
            raise HTTPException(422, '추가 조사에 참여할 전문가를 선택해 주세요.')
        result = answer(Question(question=question, expert_ids=ids, report_id=report_id,
                                 report_section_id=section['id'], report_revision=request.revision))
        research_run_id = result['run_id']
        candidates = {**evidence_from_runs([research_run_id]), **candidates}
    checkpoint()
    try:
        prompt, budget = load_prompt('report'), budget_for(llm)
        from .terminology import snapshot, prompt_rules, prompt_text
        rules = prompt_rules(json.dumps([request.instruction, section, candidates], ensure_ascii=False), snapshot())
        system = prompt_text(prompt.text, rules)
    except WikiError as error:
        raise HTTPException(422, str(error)) from None
    payload = dict(task='report_edit', instruction=request.instruction,
                   report_title=report['document']['title'], purpose=report['document']['purpose'],
                   outline=[dict(id=s['id'], title=s['title']) for s in report['document']['sections']],
                   selected_section={k: section[k] for k in ('id', 'title', 'layout', 'statements')},
                   evidence=copy.deepcopy(section['sources']))
    if not budget.fits(system, payload):
        raise HTTPException(422, '선택 구간과 기존 근거가 모델 입력 한도를 넘었습니다. 구간을 나누거나 LLM 입력 한도를 확인해 주세요.')
    for key, ev in candidates.items():
        if key in payload['evidence']:
            continue
        payload['evidence'][key] = ev
        if len(payload['evidence']) > 24 or not budget.fits(system, payload):
            del payload['evidence'][key]
    if not payload['evidence']:
        raise HTTPException(422, '이 구간에 사용할 논문 근거가 없습니다. 먼저 연구 질문으로 근거를 찾거나 추가 논문 조사를 켜 주세요.')
    stage('report_write', '선택한 구간만 작성하고 있습니다. 다른 구간은 유지됩니다.')
    try:
        result = llm.complete_json(system, payload, max_output_tokens=budget.output_tokens)
        checkpoint()
        stage('report_verify', '인용 원문과 수치·단위, 근거 연결을 검증하고 있습니다.')
        checked = grounded_sections(result, payload['evidence'])
        if len(checked) != 1:
            raise WikiError('선택 구간을 작성할 근거가 부족하거나 응답 형식이 올바르지 않습니다. 기존 보고서를 유지합니다.')
    except (LLMError, WikiError) as error:
        raise HTTPException(422, str(error).replace('기존 Wiki', '기존 보고서')) from None
    document = copy.deepcopy(report['document'])
    target = next(s for s in document['sections'] if s['id'] == section['id'])
    target['statements'] = checked[0]['statements']
    used = {key for st in target['statements'] for key in st['evidence_ids']}
    target['sources'] = {key: payload['evidence'][key] for key in used}
    usage = dict(edit_calls=1, tokens=getattr(llm, 'usage_history', [])[-1:],
                 elapsed_ms=round((time.monotonic()-start)*1000), research_run_id=research_run_id,
                 evidence_used=len(used), prompt_hash=prompt.fingerprint)
    checkpoint()
    stage('report_save', '검증된 구간을 새 버전으로 저장하고 있습니다.')
    # Only this short transaction acquires a write lock. CAS rejects concurrent changes.
    return save_report(report_id, request.revision, document, request.instruction, section['id'],
                       llm.service.model, usage)
