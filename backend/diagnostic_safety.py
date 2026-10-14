"""Child-process isolation and actual HTTP attempt accounting for diagnostics."""
import ipaddress
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import OpenerDirector, url2pathname


class DiagnosticFailure(ValueError):
    """Only fixed, locally authored descriptions go in this exception."""


def install_guard(root, live=False):
    root = Path(root).resolve()
    def local(path):
        if isinstance(path, int) or path is None:
            return
        if not Path(os.fsdecode(path)).resolve().is_relative_to(root):
            raise PermissionError('Diagnostic write outside its isolated directory was blocked')
    def audit(event, args):
        if event == 'open':
            path, mode, flags = args
            if not isinstance(path,int) and os.fsdecode(path).casefold()==os.devnull.casefold():return
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                local(path)
        elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.truncate'):
            local(args[0])
        elif event in ('os.rename', 'os.link', 'os.symlink'):
            local(args[0]); local(args[1])
        elif event == 'sqlite3.connect':
            path = os.fsdecode(args[0])
            if path != ':memory:':
                if path.startswith('file:'):
                    path = url2pathname(urlsplit(path).path)
                local(path)
        elif event == 'socket.connect' and not live:
            address = args[1]
            if isinstance(address, tuple):
                try: allowed = address[0] == 'localhost' or ipaddress.ip_address(address[0]).is_loopback
                except ValueError: allowed = False
                if not allowed: raise PermissionError('External networking is disabled for offline diagnostics')
    sys.addaudithook(audit)


class HTTPMeter:
    """Wrap the production urllib transport, including embedding retry attempts.

    Keeps only model/service, status, elapsed time and reported usage. Request and
    response bodies are never persisted. A checkpoint is saved before network I/O.
    """
    def __init__(self, routes, save, *, max_calls=80):
        self.routes = routes
        self.save = save
        self.max_calls = max_calls
        self.calls = []
        self.original = OpenerDirector.open

    def __enter__(self):
        meter = self
        def opened(opener, request, *args, **kwargs):
            url = request.full_url if hasattr(request, 'full_url') else str(request)
            try: model = json.loads(request.data or b'{}').get('model', '')
            except (ValueError, AttributeError): model = ''
            service = meter.routes.get((url, model))
            if not service:
                raise DiagnosticFailure('선택 단계에 설정되지 않은 API 호출을 차단했습니다.')
            if len(meter.calls) >= meter.max_calls:
                raise DiagnosticFailure('점검용 API 호출 상한에 도달했습니다. 결과를 확인한 뒤 필요한 단계만 재실행하세요.')
            row = dict(service=service, model=str(model)[:256], status='running', elapsed_ms=0,
                       started_at=time.time(), input_tokens=None, output_tokens=None, total_tokens=None)
            meter.calls.append(row)
            started = time.monotonic()
            meter.save()
            def finish(status, error=None):
                row.update(status=status, elapsed_ms=round((time.monotonic()-started)*1000))
                if error is not None and isinstance(getattr(error, 'code', None), int):row['http_status'] = error.code
                meter.save()
            try: response = meter.original(opener, request, *args, **kwargs)
            except BaseException as error:
                finish('failed', error)
                raise
            class Response:
                def __enter__(self):return self
                def __exit__(self, *exc):
                    response.close()
                    if row['status'] == 'running':finish('failed' if exc[0] else 'completed')
                def read(self, *a, **kw):
                    try:
                        raw = response.read(*a, **kw)
                        try:
                            payload = json.loads(raw)
                            usage = payload.get('usage', {}) if isinstance(payload, dict) else {}
                            for key, field in [('prompt_tokens','input_tokens'),('completion_tokens','output_tokens'),('total_tokens','total_tokens')]:
                                value = usage.get(key) if isinstance(usage, dict) else None
                                if type(value) is int and 0 <= value <= 2**63-1:row[field] = value
                            if row['total_tokens'] is None and all(row[k] is not None for k in ('input_tokens','output_tokens')):
                                row['total_tokens'] = row['input_tokens'] + row['output_tokens']
                        except (ValueError, TypeError):pass
                        finish('completed')
                        return raw
                    except BaseException as error:
                        finish('failed', error)
                        raise
            return Response()
        OpenerDirector.open = opened
        return self

    def __exit__(self, *exc):
        OpenerDirector.open = self.original
