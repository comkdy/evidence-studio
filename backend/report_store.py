"""Private report documents with immutable versions and short optimistic writes."""
import json
from uuid import uuid4
from fastapi import HTTPException
from .auth import current_user
from .pilot_db import connect, write_connection, now
from .research_progress import checkpoint


def read_report(report_id, version=None):
    owner = current_user()['employee_id']
    with connect() as db:
        row = db.execute('SELECT * FROM reports WHERE id=? AND owner_id=?', (report_id, owner)).fetchone()
        if not row:
            raise HTTPException(404, '보고서를 찾을 수 없습니다.')
        number = version if version is not None else row['revision']
        snapshot = db.execute('SELECT * FROM report_versions WHERE report_id=? AND revision=?', (report_id, number)).fetchone()
    if not snapshot:
        raise HTTPException(404, '보고서 버전을 찾을 수 없습니다.')
    return dict(id=report_id, revision=number, current_revision=row['revision'], updated_at=snapshot['created_at'],
                document=json.loads(snapshot['document_json']), instruction=snapshot['instruction'],
                section_id=snapshot['section_id'], model=snapshot['model'], usage=json.loads(snapshot['usage_json']))


def section_in(report, section_id):
    section = next((s for s in report['document']['sections'] if s['id'] == section_id), None)
    if not section:
        raise HTTPException(404, '선택한 보고서 구간이 없습니다.')
    return section


def check_revision(report, revision):
    if report['current_revision'] != revision:
        raise HTTPException(409, '보고서가 변경되었습니다. 최신 버전을 불러온 뒤 다시 요청해 주세요.')


def save_report(report_id, base_revision, document, instruction, section_id='', model='', usage=None):
    encoded = json.dumps(document, ensure_ascii=False)
    if len(encoded) > 2_000_000:
        raise HTTPException(422, '보고서가 너무 큽니다. 구간 수나 내용을 줄여 주세요.')
    owner, stamp = current_user()['employee_id'], now()
    with write_connection() as db:
        checkpoint()
        row = db.execute('SELECT revision FROM reports WHERE id=? AND owner_id=?', (report_id, owner)).fetchone()
        if not row:
            raise HTTPException(404, '보고서를 찾을 수 없습니다.')
        if row['revision'] != base_revision:
            raise HTTPException(409, '다른 수정이 먼저 저장되었습니다. 최신 보고서에서 다시 요청해 주세요.')
        revision = base_revision + 1
        db.execute('INSERT INTO report_versions VALUES(?,?,?,?,?,?,?,?)',
                   (report_id, revision, encoded, instruction, section_id, model, json.dumps(usage or {}), stamp))
        db.execute('UPDATE reports SET title=?,revision=?,updated_at=? WHERE id=?', (document['title'], revision, stamp, report_id))
    return read_report(report_id, revision)


def create_report(title, purpose):
    report_id, stamp = str(uuid4()), now()
    document = dict(title=title, purpose=purpose, sections=[
        dict(id=str(uuid4()), title=name, layout=layout, statements=[], sources={})
        for name, layout in [('핵심 요약', 'prose'), ('주요 결과', 'bullets'), ('비교와 조건', 'table'), ('적용 한계와 후속 검토', 'prose')]])
    with write_connection() as db:
        db.execute('INSERT INTO reports VALUES(?,?,?,?,?,?)', (report_id, current_user()['employee_id'], title, 1, stamp, stamp))
        db.execute('INSERT INTO report_versions VALUES(?,?,?,?,?,?,?,?)',
                   (report_id, 1, json.dumps(document, ensure_ascii=False), '보고서 생성', '', '', '{}', stamp))
    return read_report(report_id)


def focus_context(report_id, section_id, revision):
    if not report_id:
        if section_id or revision is not None:
            raise HTTPException(422, '보고서와 구간을 함께 선택해 주세요.')
        return []
    if not section_id or revision is None:
        raise HTTPException(422, '보고서 구간과 버전을 선택해 주세요.')
    report = read_report(report_id)
    check_revision(report, revision)
    section = section_in(report, section_id)
    return [dict(question='선택한 보고서 구간의 문맥 (논문 근거가 아님): '+report['document']['title']+' / '+section['title'],
                 answer=(report['document']['purpose']+'\n'+'\n'.join(s['text'] for s in section['statements']))[:2200])]
