"""Company Chat Completions client. No public endpoint or implicit credentials."""
import json
import ssl
import threading
import time
from contextlib import contextmanager
from collections import deque
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler
from .ai_settings import load_ai_settings
from .embedding_client import NoRedirect
from .research_progress import checkpoint, stage


class LLMError(ValueError):
    pass


class LLMTimeoutError(LLMError):
    pass


class LLMCapacityError(LLMError):
    pass


class ModelCallGate:
    """One shared process-wide limit for Wiki, lint, and all answer endpoints."""
    def __init__(self):
        self.condition = threading.Condition()
        self.active = 0
        self.waiters = deque()

    @contextmanager
    def slot(self, limit, deadline):
        waiting = False
        ticket = object()
        with self.condition:
            self.waiters.append(ticket)
            try:
                while self.active >= limit or self.waiters[0] is not ticket:
                    checkpoint()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise LLMCapacityError('사내 LLM 호출 대기 시간이 초과되었습니다. 잠시 후 다시 시도하세요.')
                    if not waiting:
                        stage('llm_wait', '사내 LLM의 다른 요청이 끝나기를 기다리고 있습니다.')
                        waiting = True
                    self.condition.wait(min(0.1, remaining))
                checkpoint()
                if time.monotonic() >= deadline:
                    raise LLMCapacityError('사내 LLM 호출 대기 시간이 초과되었습니다. 잠시 후 다시 시도하세요.')
                self.active += 1
            finally:
                self.waiters.remove(ticket)
                self.condition.notify_all()
        try:
            yield waiting
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()

    def busy(self):
        with self.condition:
            return self.active > 0


_model_gate = ModelCallGate()


def model_requests_active():
    return _model_gate.busy()


class CompanyLLM:
    def __init__(self, service=None, *, limits=None):
        settings = load_ai_settings()
        self.service = service or settings.llm
        self.limits = limits or settings.limits
        self.timeout_seconds = self.limits.llm_timeout_seconds
        self.usage_history = []
        self.call_history = []

    @property
    def configured(self):
        return bool(self.service.base_url and self.service.model and self.service.api_key.get_secret_value())

    @property
    def identity(self):
        import hashlib
        return self.service.model + ':' + hashlib.sha256(self.service.base_url.encode()).hexdigest()[:16]

    def complete_json(self, system, payload, *, deadline=None):
        """Bound local queueing and I/O waits; never automatically repeat generation."""
        checkpoint()
        started = time.monotonic()
        status = 'failed'
        try:
            if not self.configured:
                raise LLMError('사내 LLM 주소·모델·API key를 설정해야 합니다.')
            queue_deadline = started + self.limits.llm_queue_timeout_seconds
            if deadline is not None:
                queue_deadline = min(queue_deadline, deadline)
            with _model_gate.slot(self.limits.llm_max_concurrent_requests, queue_deadline) as waited:
                checkpoint()
                if waited:
                    stage('llm', '사내 LLM이 응답을 생성하고 있습니다.')
                timeout = self.timeout_seconds
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise LLMTimeoutError('심층 분석 시간 한도에 도달했습니다.')
                    timeout = min(timeout, remaining)
                result = self._request_json(system, payload, timeout)
                checkpoint()
                if deadline is not None and time.monotonic() >= deadline:
                    raise LLMTimeoutError('심층 분석 시간 한도에 도달하여 늦게 도착한 결과를 사용하지 않았습니다.')
                status = 'completed'
                return result
        except LLMError as error:
            status = type(error).__name__
            raise
        finally:
            self.call_history.append(dict(status=status, elapsed_ms=round((time.monotonic()-started)*1000),
                                          input_chars=len(system)+len(json.dumps(payload,ensure_ascii=False))))

    def _request_json(self, system, payload, timeout):
        checkpoint()
        if not self.configured:
            raise LLMError('사내 LLM 주소·모델·API key를 설정해야 합니다.')
        endpoint = self.service.base_url.rstrip('/')
        if not endpoint.endswith('/chat/completions'):
            endpoint += '/chat/completions'
        parsed = urlsplit(endpoint)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise LLMError('사내 LLM 주소 설정을 확인하세요.')
        key = self.service.api_key.get_secret_value()
        if any(ord(c) < 32 or ord(c) == 127 for c in key):
            raise LLMError('LLM 인증 설정을 확인하세요.')
        body = {'model': self.service.model, 'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]}
        try:
            req = Request(endpoint, data=json.dumps(body, ensure_ascii=False).encode(),
                          headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'}, method='POST')
            with build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context())).open(req, timeout=timeout) as response:
                raw = response.read(2_000_001)
            checkpoint()
            if len(raw) > 2_000_000:
                raise LLMError('LLM 응답이 너무 큽니다.')
            envelope = json.loads(raw)
            usage = envelope.get('usage')
            self.usage_history.append({k: v for k, v in (usage.items() if isinstance(usage, dict) else [])
                                       if k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                                       and type(v) is int and v >= 0})
            choice = envelope['choices'][0]
            if choice.get('finish_reason') not in (None, 'stop'):
                raise LLMError('LLM 응답이 완성되지 않았습니다. 기존 Wiki를 유지합니다.')
            content = choice['message']['content'].strip()
            if content.startswith('```'):
                content = content.split('\n', 1)[1].rsplit('```', 1)[0]
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except HTTPError as error:
            code = error.code
            error.close()
            if code in (408, 504):
                raise LLMTimeoutError(f'사내 LLM 또는 게이트웨이가 대기 시간을 초과했습니다 (HTTP {code}). 서버·프록시 timeout도 확인하세요. 기존 Wiki를 유지합니다.') from None
            if code in (429, 503):
                raise LLMCapacityError(f'사내 LLM이 혼잡하거나 일시적으로 사용할 수 없습니다 (HTTP {code}). 잠시 후 다시 시도하세요. 기존 Wiki를 유지합니다.') from None
            raise LLMError(f'사내 LLM 호출 실패 (HTTP {code}). 기존 Wiki를 유지합니다.') from None
        except LLMError:
            raise
        except TimeoutError:
            raise LLMTimeoutError(f'사내 LLM 응답 대기 시간이 초과되었습니다 ({timeout:g}초 I/O 대기 한도). LLM_TIMEOUT_SECONDS와 서버·프록시 설정을 확인하세요. 기존 Wiki를 유지합니다.') from None
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise LLMTimeoutError(f'사내 LLM 연결 대기 시간이 초과되었습니다 ({timeout:g}초 I/O 대기 한도). 기존 Wiki를 유지합니다.') from None
            raise LLMError('사내 LLM에 연결하지 못했습니다. 주소·인증·네트워크를 확인하세요. 기존 Wiki를 유지합니다.') from None
        except (HTTPException, OSError):
            raise LLMError('사내 LLM 연결이 중단되었습니다. 서버·프록시 상태를 확인하세요. 기존 Wiki를 유지합니다.') from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LLMError('사내 LLM의 JSON 응답 형식을 확인하세요. 기존 Wiki를 유지합니다.') from None
