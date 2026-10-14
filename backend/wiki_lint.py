"""Scheduled integrity checks and LLM evidence review; never auto-rewrites facts."""
from __future__ import annotations
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4
from .pilot_db import connect, now
from .llm_client import CompanyLLM, LLMError
from .wiki_store import WikiStore, WikiError, atomic_write, safe_path, parse_markdown, digest
from .wiki_engine import snapshot, current, enqueue, escaped
from .wiki_policy import load_prompt

LINT_INTERVAL = timedelta(hours=24)
FULL_REVIEW_INTERVAL = timedelta(days=30)
MAX_LLM_CALLS = 12
MAX_REVIEW_CHARS = 60000


def validate_grounding(row, body, state, catalogue, store):
    problems = []
    refs = json.loads(row['refs_json'])
    by_path = {r['path']:r['id'] for r in catalogue.values()}
    if not current(row,state,catalogue):
        problems.append('원천 근거 또는 전문가 범위가 바뀌어 갱신 필요')
    papers = [state['papers'][p] for p in refs['paper_ids'] if p in state['papers']]
    claims = {c['id']:c for p in papers for c in p['claims']}
    if any(cid not in claims for cid in refs['claim_ids']):
        problems.append('삭제되었거나 범위 밖인 claim')
    docs = {d['id'] for p in papers for d in p['documents']}
    for href in re.findall(r'\]\(([^\s)]+)\)', body):
        if href.startswith('/api/pilot/claims/'):
            if href.rsplit('/',1)[-1] not in refs['claim_ids']:
                problems.append('frontmatter 근거에 없는 claim 링크')
        elif href.startswith('/api/pilot/documents/'):
            if href.split('/')[4] not in docs:
                problems.append('담당 근거 밖의 PDF 링크')
        elif href.startswith('#'):
            continue
        elif '://' in href or href.startswith('/'):
            problems.append('허용하지 않은 외부 링크')
        else:
            try:
                target = safe_path(store.root,str(Path(row['path']).parent/href.split('#')[0]))
                relative = target.relative_to(store.root.resolve()).as_posix()
                if relative not in by_path or not target.is_file():
                    problems.append('대상 Wiki가 없는 링크')
                elif by_path[relative] not in refs['dependencies']:
                    problems.append('등록된 의존 관계에 없는 Wiki 링크')
            except (WikiError,ValueError):
                problems.append('Wiki 저장소 밖의 링크')
    for span in refs['source_spans']:
        page = next((pg for p in papers for pg in p['pages'] if pg['id']==span['page_id']),None)
        if not page or page['text'][span['start_offset']:span['end_offset']] != span['quote']:
            problems.append('원문 인용 위치 불일치')
    return sorted(set(problems))


def run_lint(database=None, *, due_only=False, use_llm=True, llm=None):
    from .terminology import snapshot as term_snapshot, prompt_rules, prompt_text
    prompt = load_prompt('lint')
    glossary=term_snapshot(database)
    store = WikiStore(database)
    llm = llm or CompanyLLM()
    stamp = now()
    # Reusing a previous review requires the same model AND writing rules.
    model=(llm.identity if use_llm and llm.configured else 'offline')+':rules:'+prompt.fingerprint+':terms:'+str(glossary['revision'])
    with connect(database) as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT * FROM wiki_lint_runs ORDER BY created_at DESC LIMIT 1').fetchone()
        if previous:
            elapsed = datetime.now(timezone.utc)-datetime.fromisoformat(previous['created_at'])
            if (previous['state']=='running' and elapsed<timedelta(hours=4)) or (due_only and elapsed<LINT_INTERVAL and previous['model']==model):
                return {'status':'not_due','next_check_at':(datetime.fromisoformat(previous['created_at'])+LINT_INTERVAL).isoformat()}
        run_id = 'lint-'+uuid4().hex
        report_path = f'.reports/{run_id}.md'
        db.execute('INSERT INTO wiki_lint_runs VALUES(?,?,?,?,?,?,?,?,?)',
            (run_id,stamp,None,'running',model, '',report_path,'[]',0))
        checks={r['page_id']:dict(r) for r in db.execute('SELECT * FROM wiki_lint_checks')}
        db.commit()
    findings = []; checked = 0; eligible = 0; calls=0; review_chars=0
    try:
        state = snapshot(database)
        catalogue = {r['id']:r for r in store.rows()}
        rows = [r for r in catalogue.values() if r['status']!='archived']
        valid_rows = [r for r in rows if current(r,state,catalogue)]
        if store.index_problems(valid_rows):
            findings.append(dict(page_id='index.md',severity='error',issue='Wiki 목차가 없거나 현재 문서 목록·상태와 일치하지 않습니다.',suggestion='sync를 실행하여 목차를 다시 생성하세요.'))
        def priority(r):
            old=checks.get(r['id'])
            changed=not old or old['content_hash']!=r['content_hash'] or old['model']!=model
            return (not changed,old['checked_at'] if old else '',r['id'])
        rows.sort(key=priority)
        fingerprint = digest([(r['id'],r['content_hash']) for r in rows])
        for row in rows:
            try:
                _, body = store.read(row)
                problems = validate_grounding(row,body,state,catalogue,store)
                findings += [dict(page_id=row['id'],severity='error',issue=p,suggestion='근거와 연결 설정 확인 후 sync 실행') for p in problems]
                if problems or row['kind']=='expert_index' or row['status']!='ready':
                    continue
                eligible += 1
                if not use_llm or not llm.configured:
                    continue
                old=checks.get(row['id'])
                unchanged=old and old['content_hash']==row['content_hash'] and old['model']==model
                if unchanged and old['next_offset']==0 and datetime.now(timezone.utc)-datetime.fromisoformat(old['checked_at'])<FULL_REVIEW_INTERVAL:
                    findings.extend(json.loads(old['findings_json']))
                    checked+=1
                    continue
                if calls>=MAX_LLM_CALLS or review_chars>=MAX_REVIEW_CHARS:
                    continue
                refs = json.loads(row['refs_json'])
                claims = [dict(id=c['id'],summary=c['summary'],kind=c['kind'],scope=c['scope'],
                    measurements=[dict(label=m['label'],raw_value=m['raw_value'],kind=m['kind'],sample=m['sample'],
                        conditions=[dict(name=x['name'],raw_value=x['raw_value']) for x in m['conditions']]) for m in c['measurements']],
                    quotes=[s['quote'] for s in c['sources']]) for p in refs['paper_ids'] for c in state['papers'][p]['claims'] if c['id'] in refs['claim_ids']]
                page_findings=json.loads(old['findings_json']) if unchanged and old['next_offset'] else []
                start_offset=old['next_offset'] if unchanged else 0
                next_offset=start_offset
                # Long pages resume next cycle; no unseen tail is marked checked.
                for start in range(start_offset,len(body),14000):
                    if calls>=MAX_LLM_CALLS or review_chars>=MAX_REVIEW_CHARS:
                        break
                    piece = body[start:start+14000]
                    rules=prompt_rules(piece,glossary)
                    system=prompt_text(prompt.text,rules)
                    packet_chars=len(piece)+len(json.dumps(claims,ensure_ascii=False))+len(json.dumps(refs['source_spans'],ensure_ascii=False))+len(system)
                    if review_chars+packet_chars>MAX_REVIEW_CHARS:
                        break
                    calls+=1; review_chars+=packet_chars
                    reply = llm.complete_json(system, dict(wiki=piece,claims=claims,source_quotes=refs['source_spans']))
                    items = reply.get('findings')
                    if not isinstance(items,list) or len(items)>12:
                        raise WikiError('LLM lint 응답 형식이 올바르지 않습니다.')
                    for item in items:
                        if not isinstance(item,dict) or item.get('severity') not in ('warning','error') or not all(isinstance(item.get(k),str) and 1<=len(item[k])<=1600 for k in ('quote','issue','suggestion')) or item['quote'] not in piece:
                            raise WikiError('LLM lint가 검사 문서에 없는 구절을 지적했습니다.')
                        page_findings.append(dict(page_id=row['id'],**item))
                    next_offset=min(len(body),start+14000)
                complete=next_offset>=len(body)
                if next_offset>start_offset:
                    with connect(database) as db:
                        db.execute('INSERT OR REPLACE INTO wiki_lint_checks VALUES(?,?,?,?,?,?)',
                            (row['id'],row['content_hash'],stamp,model,json.dumps(page_findings,ensure_ascii=False),0 if complete else next_offset)); db.commit()
                findings.extend(page_findings)
                checked += int(complete)
            except (WikiError, LLMError) as error:
                findings.append(dict(page_id=row['id'],severity='error',issue=str(error),suggestion='기존 문서를 보존했습니다. 검토 후 다시 점검하세요.'))
                if isinstance(error,LLMError):
                    use_llm = False  # Avoid dozens of calls after one connection failure.
        status = 'completed' if checked==eligible else 'partial'
    except Exception:
        status = 'failed'; fingerprint = ''
        findings.append(dict(page_id='',severity='error',issue='Wiki 점검을 완료하지 못했습니다.',suggestion='서버 로그 및 파일 상태를 확인하세요.'))
    text = '# Wiki 정기 점검\n\n'+stamp+'\n\n'
    text += '공통 작성 지침 SHA-256: '+prompt.policy_hash+'\n\n'
    text += f'주기: 24시간 (앱 실행 중) · 유효한 LLM 검토: {checked}/{eligible}개 문서 · 이번 호출: {calls}/{MAX_LLM_CALLS}회\n\n'
    text += '변경 문서 우선 · 미변경 문서는 30일 경과 후 순환 점검 · 회차당 입력 60,000자 이내 · 예산 초과 문서는 다음 회차로 이월\n\n'
    if not llm.configured:
        text += '사내 LLM 미설정: 링크·범위·갱신 검사만 수행했습니다. 내용 검토는 대기 중입니다.\n\n'
    text += '\n'.join(f'- **{f["severity"]}** · {escaped(f["page_id"])}: {escaped(f["issue"])} — {escaped(f["suggestion"])}' for f in findings) or '구조 검사에서 문제를 발견하지 못했습니다.\n'
    text += '\n\n점검 결과는 검토 의견이며, 기존 Wiki를 자동 수정하지 않습니다.\n'
    atomic_write(safe_path(store.root,report_path),text)
    atomic_write(store.root/'lint.md',text)
    with connect(database) as db:
        db.execute('UPDATE wiki_lint_runs SET finished_at=?,state=?,fingerprint=?,findings_json=?,llm_checked=? WHERE id=?',
            (now(),status,fingerprint,json.dumps(findings,ensure_ascii=False),checked,run_id)); db.commit()
    return {'status':status,'findings':len(findings),'llm_checked':checked,'llm_calls':calls,'review_chars':review_chars,'report':str(store.root/'lint.md')}


def adopt(page_id,database=None):
    store=WikiStore(database); row=store.record(page_id)
    if not row:
        raise WikiError('등록된 Wiki ID가 필요합니다.')
    text=safe_path(store.root,row['path']).read_text(encoding='utf-8')
    meta,body=parse_markdown(text)
    refs=json.loads(row['refs_json'])
    if meta.get('refs')!=refs or meta.get('id')!=row['id'] or meta.get('scope')!=row['scope'] or meta.get('expert_id')!=row['expert_id'] or meta.get('revision')!=row['revision']:
        raise WikiError('adopt는 본문 편집만 허용합니다. 근거·범위·버전은 변경할 수 없습니다.')
    state=snapshot(database); catalogue={r['id']:r for r in store.rows()}
    problems=validate_grounding(row,body,state,catalogue,store)
    if problems:
        raise WikiError('; '.join(problems))
    result=store.publish(page_id,row['path'],row['title'],body,refs,row['input_hash'],scope=row['scope'],
        expert_id=row['expert_id'],kind=row['kind'],status=row['status'],producer='manual-reviewed',reason='manual Markdown edit',adopt=True)
    with connect(database) as db:
        enqueue(db,'adopted Wiki edit; refresh dependent pages'); db.commit()
    return {'status':'adopted','id':result['id'],'revision':result['revision']}
