"""Read-only latency/call summaries; never send models a prompt or export questions."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def distribution(values):
    ordered = sorted(float(value) for value in values if value is not None)
    def percentile(p):
        if not ordered:
            return None
        position = (len(ordered)-1)*p
        low = int(position)
        return round(ordered[low]+(ordered[min(low+1,len(ordered)-1)]-ordered[low])*(position-low), 2)
    return {'count':len(ordered),'p50':percentile(.5),'p95':percentile(.95)}


def report(database, *, since=None):
    path = Path(database).resolve()
    if not path.is_file():
        raise ValueError('측정 DB 파일이 없습니다.')
    threshold = 0
    if since:
        instant = datetime.fromisoformat(since.replace('Z','+00:00'))
        threshold = (instant if instant.tzinfo else instant.replace(tzinfo=timezone.utc)).timestamp()
    db = sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        needed = {'research_request_metrics','research_requests','model_call_metrics','work_stage_metrics'}
        if not needed.issubset(tables):
            raise ValueError('처리량 측정 schema가 없는 DB입니다. 서버를 한 번 시작한 후 측정하세요.')
        requests = [dict(r) for r in db.execute('''SELECT m.initial_ms,m.final_ms,r.cache_hit,r.status,r.job_id,
            json_extract(r.result_json,'$.research.validation_status') AS validation
            FROM research_request_metrics m JOIN research_requests r ON r.id=m.request_id
            WHERE unixepoch(m.created_at)>=?''', (threshold,))]
        calls = [dict(r) for r in db.execute('SELECT * FROM model_call_metrics WHERE created_at>=?', (threshold,))]
        stages = [dict(r) for r in db.execute('SELECT * FROM work_stage_metrics WHERE created_at>=?', (threshold,))]
    finally:
        db.close()
    by_role = defaultdict(list)
    by_job = Counter()
    for call in calls:
        by_role[call['role']].append(call)
        if call['job_id']:
            by_job[call['job_id']] += 1
    complete = [r for r in requests if r['status']=='completed']
    return dict(version=1, since=since, units='milliseconds', requests=len(requests),
        states=dict(Counter(r['status'] for r in requests)),
        initial_evidence_ms=distribution(r['initial_ms'] for r in requests),
        final_answer_ms=distribution(r['final_ms'] for r in complete),
        uncached_final_answer_ms=distribution(r['final_ms'] for r in complete if not r['cache_hit']),
        cache_hits=sum(r['cache_hit'] for r in requests),
        completed_validation=dict(Counter(r['validation'] or 'not_generated' for r in complete)),
        model_operations_per_completed_request=distribution(by_job[r['job_id']] if r['job_id'] else 0 for r in complete),
        models={role:dict(operations=len(rows), statuses=dict(Counter(r['status'] for r in rows)),
            queue_wait_ms=distribution(r['queued_ms'] for r in rows),
            provider_operation_ms=distribution(r['elapsed_ms'] for r in rows)) for role,rows in by_role.items()},
        stages={kind:dict(attempts=len(rows), queue_wait_ms=distribution(r['queued_ms'] for r in rows),
            duration_ms=distribution(r['elapsed_ms'] for r in rows)) for kind,rows in
            ((kind,[r for r in stages if r['kind']==kind]) for kind in sorted({r['kind'] for r in stages}))},
        limitations=[
            '운영 기록의 관측값이며 같은 질문·조건·모델 부하를 맞춘 대조 실험 결과가 아닙니다.',
            '완료된 요청의 최종 지연만 집계합니다. 실패·취소 건수도 함께 확인하세요.',
            '모델 자동 검토 통과는 사람의 과학적 정확성 평가를 대체하지 않습니다.',
            '모델 operation은 클라이언트 호출 단위이며 embedding 내부 HTTP 재시도가 포함될 수 있습니다.',
        ])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--since',help='ISO 8601 timestamp; omitted means all stored measurements')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        result=report(args.database,since=args.since)
    except (ValueError,sqlite3.Error):
        parser.exit(2,'측정 DB·schema·시각 형식을 확인하세요.\n')
    output=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(output+'\n',encoding='utf-8')
    print(output)


if __name__=='__main__':
    main()
