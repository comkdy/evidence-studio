"""Owner-only, immutable retrieval and answer snapshots; never knowledge inputs."""
import json
from fastapi import HTTPException
from .auth import current_user
from .pilot_db import connect, now


def save_run(run_id, request, *, model_identity, prompt, evidence, trace, result,
             elapsed_ms, status='completed'):
    owner = current_user()['employee_id']
    encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    with connect() as db:
        db.execute('''INSERT INTO answer_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
            run_id, owner, now(), request.question, request.mode, status,
            model_identity, prompt.policy_hash if prompt else '',
            prompt.fingerprint if prompt else '', encode(evidence), encode(trace),
            encode(result), elapsed_ms))
        db.commit()


def read_run(run_id):
    owner = current_user()['employee_id']
    with connect() as db:
        row = db.execute('SELECT * FROM answer_runs WHERE id=? AND owner_id=?',
                         (run_id, owner)).fetchone()
    if row is None:
        raise HTTPException(404, '답변 실행 기록을 찾을 수 없습니다.')
    result = dict(row)
    for key in ('evidence', 'trace', 'result'):
        result[key] = json.loads(result.pop(key + '_json'))
    return result
