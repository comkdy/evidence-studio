"""Company Chat Completions client. No public endpoint or implicit credentials."""
import json
import ssl
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler
from .ai_settings import load_ai_settings
from .embedding_client import NoRedirect


class LLMError(ValueError):
    pass


class CompanyLLM:
    def __init__(self, service=None):
        self.service = service or load_ai_settings().llm
        self.timeout_seconds = 90
        self.usage_history = []

    @property
    def configured(self):
        return bool(self.service.base_url and self.service.model and self.service.api_key.get_secret_value())

    @property
    def identity(self):
        import hashlib
        return self.service.model + ':' + hashlib.sha256(self.service.base_url.encode()).hexdigest()[:16]

    def complete_json(self, system, payload):
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
            with build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context())).open(req, timeout=self.timeout_seconds) as response:
                raw = response.read(2_000_001)
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
            raise LLMError(f'사내 LLM 호출 실패 (HTTP {code}). 기존 Wiki를 유지합니다.') from None
        except LLMError:
            raise
        except (URLError, HTTPException, OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LLMError('사내 LLM 연결 또는 JSON 응답 형식을 확인하세요. 기존 Wiki를 유지합니다.') from None
