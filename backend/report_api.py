"""Owner-scoped report API. All authenticated roles may author their own reports."""
import asyncio
import copy
import json
import queue
import threading
from contextvars import copy_context
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, ConfigDict
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from .auth import current_user
from .pilot_db import connect
from .report_store import create_report, read_report, section_in, check_revision, save_report
from .report_engine import edit_section, evidence_from_runs
from .report_export import render_html
from .research_progress import ResearchControl, ResearchCancelled, _current
from .research_stream import _slots

router = APIRouter(prefix='/api/pilot/reports', tags=['Reports'])


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class NewReport(Strict):
    title: str = Field(min_length=1, max_length=160)
    purpose: str = Field(default='', max_length=1000)


class Revision(Strict):
    revision: int = Field(ge=1)


class Restore(Revision):
    version: int = Field(ge=1)


class SectionChange(Revision):
    action: Literal['add', 'update', 'delete']
    section_id: str = Field(default='', max_length=80)
    title: str = Field(default='새 구간', min_length=1, max_length=160)
    layout: Literal['prose', 'bullets', 'table'] = 'prose'


class EditRequest(Revision):
    section_id: str = Field(min_length=1, max_length=80)
    instruction: str = Field(min_length=1, max_length=1000)
    run_ids: list[str] = Field(default_factory=list, max_length=6)
    research: bool = False
    selection: Literal['auto', 'manual'] = 'auto'
    expert_ids: list[str] = Field(default_factory=list, max_length=20)


@router.get('')
def list_reports():
    with connect() as db:
        return [dict(row) for row in db.execute('SELECT id,title,revision,updated_at FROM reports WHERE owner_id=? ORDER BY updated_at DESC LIMIT 100', (current_user()['employee_id'],))]


@router.post('', status_code=201)
def new_report(request: NewReport):
    return create_report(request.title, request.purpose)


@router.get('/{report_id}')
def get_report(report_id: str, version: int | None = Query(default=None, ge=1)):
    return read_report(report_id, version)


@router.get('/{report_id}/versions')
def versions(report_id: str, before: int | None = Query(default=None, ge=1)):
    report = read_report(report_id)
    with connect() as db:
        return [dict(row) for row in db.execute('SELECT revision,instruction,section_id,model,created_at FROM report_versions WHERE report_id=? AND revision<? ORDER BY revision DESC LIMIT 100', (report_id, before or report['current_revision']+1))]


@router.post('/{report_id}/restore')
def restore(report_id: str, request: Restore):
    report = read_report(report_id)
    check_revision(report, request.revision)
    previous = read_report(report_id, request.version)
    return save_report(report_id, request.revision, previous['document'], 'v'+str(request.version)+' 복원')


@router.patch('/{report_id}/sections')
def change_section(report_id: str, request: SectionChange):
    report = read_report(report_id)
    check_revision(report, request.revision)
    doc = copy.deepcopy(report['document'])
    if request.action == 'add':
        if len(doc['sections']) >= 20:
            raise HTTPException(422, '보고서는 최대 20개 구간으로 구성할 수 있습니다.')
        doc['sections'].append(dict(id=str(uuid4()), title=request.title, layout=request.layout, statements=[], sources={}))
    else:
        section_in(report, request.section_id)
        if request.action == 'delete':
            if len(doc['sections']) == 1:
                raise HTTPException(422, '보고서에는 최소 한 구간이 필요합니다.')
            doc['sections'] = [s for s in doc['sections'] if s['id'] != request.section_id]
        else:
            target = next(s for s in doc['sections'] if s['id'] == request.section_id)
            target.update(title=request.title, layout=request.layout)
    return save_report(report_id, request.revision, doc, {'add':'구간 추가', 'update':'구간 제목·형식 변경', 'delete':'구간 삭제'}[request.action], request.section_id)


@router.get('/{report_id}/export')
def export(report_id: str, revision: int | None = Query(default=None, ge=1)):
    report = read_report(report_id, revision)
    return HTMLResponse(render_html(report), headers={
        'Content-Disposition': 'attachment; filename="evidence-studio-report-v'+str(report['revision'])+'.html"',
        'Cache-Control': 'no-store', 'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'"})


@router.post('/{report_id}/edit-stream')
async def stream_edit(report_id: str, request: EditRequest):
    report = await run_in_threadpool(read_report, report_id)
    check_revision(report, request.revision)
    section_in(report, request.section_id)
    await run_in_threadpool(evidence_from_runs, request.run_ids)
    if request.research and request.selection == 'manual' and not request.expert_ids:
        raise HTTPException(422, '추가 조사에 참여할 전문가를 선택해 주세요.')
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, '현재 작성 작업이 많습니다. 잠시 후 다시 요청해 주세요.')
    events, done = queue.SimpleQueue(), threading.Event()
    control, context = ResearchControl(events.put), copy_context()

    def work():
        token = _current.set(control)
        try:
            result = edit_section(report_id, request)
            events.put(dict(type='report_result', result=result))
        except ResearchCancelled:
            events.put(dict(type='cancelled'))
        except HTTPException as error:
            events.put(dict(type='error', message=str(error.detail), status=error.status_code))
        except Exception:
            events.put(dict(type='error', message='보고서 수정을 완료하지 못했습니다. 저장된 버전을 다시 확인해 주세요.'))
        finally:
            _current.reset(token)
            _slots.release()
            done.set()
    try:
        threading.Thread(target=context.run, args=(work,), daemon=True).start()
    except Exception:
        _slots.release()
        raise

    async def stream():
        try:
            while not done.is_set() or not events.empty():
                try:
                    item = events.get_nowait()
                except queue.Empty:
                    yield '\n'
                    await asyncio.sleep(.15)
                    continue
                yield json.dumps(item, ensure_ascii=False)+'\n'
        finally:
            control.cancelled.set()
    return StreamingResponse(stream(), media_type='application/x-ndjson',
        headers={'Cache-Control':'no-store', 'X-Accel-Buffering':'no'}, background=BackgroundTask(control.cancelled.set))
