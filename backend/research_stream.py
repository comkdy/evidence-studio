"""NDJSON answer stages and final results; aborting the stream cancels later work."""
import asyncio
import json
import queue
import threading
from contextvars import copy_context
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from .conversation_context import conversation_context, contextual_query
from .research_progress import ResearchControl, ResearchCancelled, _current, stage, checkpoint
from .wiki_api import Question, recommendations, answer

router = APIRouter(prefix='/api/pilot', tags=['Research conversation'])
_slots = threading.BoundedSemaphore(4)


class ConversationRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    expert_ids: list[str] = Field(default_factory=list, max_length=20)
    selection: Literal['auto', 'manual'] = 'auto'
    history_run_ids: list[str] = Field(default_factory=list, max_length=6)
    report_id: str | None = Field(default=None, max_length=80)
    report_section_id: str | None = Field(default=None, max_length=80)
    report_revision: int | None = Field(default=None, ge=1)


@router.post('/ask-stream')
async def ask_stream(request: ConversationRequest):
    if not request.question.strip():
        raise HTTPException(422, '질문을 입력해 주세요.')
    if request.selection == 'manual' and not request.expert_ids:
        raise HTTPException(422, '질문할 전문가를 선택해 주세요.')
    # Validate ownership before sending headers or calling any external model.
    from starlette.concurrency import run_in_threadpool
    conversation = await run_in_threadpool(conversation_context, request.history_run_ids)
    from .report_store import focus_context
    conversation += await run_in_threadpool(focus_context, request.report_id, request.report_section_id, request.report_revision)
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, '현재 답변 작업이 많습니다. 잠시 후 다시 질문해 주세요.')
    events = queue.SimpleQueue()
    done = threading.Event()
    control = ResearchControl(events.put)
    context = copy_context()

    def work():
        token = _current.set(control)
        try:
            stage('context', '앞선 대화의 주제와 현재 질문을 확인하고 있습니다.')
            ids = list(dict.fromkeys(request.expert_ids))
            names = []
            if request.selection == 'auto':
                stage('recommend', '질문에 맞는 전문가를 찾고 있습니다.')
                chosen = recommendations(contextual_query(request.question, conversation))
                checkpoint()
                profiles = chosen['experts'][:2]
                ids = [p['id'] for p in profiles]
                names = [p['name'] for p in profiles]
                if not ids:
                    raise HTTPException(422, '질문에 맞는 전문가를 찾지 못했습니다. 직접 선택해 주세요.')
            if not names:
                from .wiki_api import load_catalogue
                _, state, _ = load_catalogue()
                names = [state['experts'][i]['name'] for i in ids if i in state['experts']]
            events.put({'type': 'experts', 'ids': ids, 'names': names})
            checkpoint()
            result = answer(Question(question=request.question, expert_ids=ids,
                                     history_run_ids=request.history_run_ids, report_id=request.report_id,
                                     report_section_id=request.report_section_id, report_revision=request.report_revision))
            checkpoint()
            events.put({'type': 'result', 'result': result})
        except ResearchCancelled:
            events.put({'type': 'cancelled'})
        except HTTPException as error:
            if not control.cancelled.is_set():
                events.put({'type': 'error', 'message': str(error.detail), 'status': error.status_code})
        except Exception:
            if not control.cancelled.is_set():
                events.put({'type': 'error', 'message': '답변 준비를 완료하지 못했습니다. 다시 시도해 주세요.'})
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
                    # Heartbeats keep the connection active without invented progress.
                    yield '\n'
                    await asyncio.sleep(0.15)
                    continue
                yield json.dumps(item, ensure_ascii=False) + '\n'
        finally:
            control.cancelled.set()

    return StreamingResponse(stream(), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'},
                             background=BackgroundTask(control.cancelled.set))
