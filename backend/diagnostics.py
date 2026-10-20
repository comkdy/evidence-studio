"""Select isolated checks: python -m backend.diagnostics --list.

No server startup, watcher, production DB connection, or implicit live API call.
Every stage gets a separate child process and disposable directory.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .ai_settings import read_ai_environment
from .diagnostic_report import write_report

ROOT = Path(__file__).resolve().parents[1]
STEPS = {
    'unit': ('백엔드 단위·회귀 테스트', '기존 Python 테스트 전체 · 외부 API 없음'),
    'ingest': ('PDF 추출·DB 저장', '합성 PDF, 원문 위치, 중복 파일 재사용'),
    'curation': ('Claim·수치·조건 연결', '검증된 JSON 가져오기 · 자동 추출 테스트 아님'),
    'chunking': ('문맥 분할', '오프라인 구조 분할 / --live 시 사내 embedding 문맥 분할'),
    'llm': ('기본 LLM 연동', '--live 필요 · JSON 응답 1회'),
    'llm-light': ('경량 LLM 연동', '--live 필요 · 별도 경량 모델 설정 시 JSON 응답 1회'),
    'embedding': ('Embedding 연동', '--live 필요 · 벡터 형식·차원·유사도'),
    'reranker': ('Reranker 연동', '--live 필요 · 응답 계약·관련 문서 순위'),
    'wiki': ('Wiki 생성·갱신', '--live 필요 · 생성·재사용·특화 노트·신규 논문 반영'),
    'answer': ('질의응답 전체 흐름', '--live 필요 · PDF→구조화 근거→Wiki→심층 답변·인용'),
}
LIVE_STEPS = {'llm','llm-light','embedding','reranker','wiki','answer'}
# Explicit empty overrides prevent the project's real .env from leaking into
# offline stages, even if a production helper loads it independently.
DEFAULTS = dict.fromkeys('''LLM_BASE_URL LLM_BASE_MODEL LLM_MODEL LLM_API_KEY LLM_LIGHT_BASE_URL LLM_LIGHT_MODEL LLM_LIGHT_API_KEY
LLM_TOKENIZER_PATH LLM_LIGHT_TOKENIZER_PATH EMBEDDING_BASE_URL EMBEDDING_MODEL EMBEDDING_API_KEY
EMBEDDING_ENDPOINT EMBEDDING_CA_BUNDLE EMBEDDING_CONTRACT_PATH EMBEDDING_MODEL_REVISION EMBEDDING_INPUT_PREFIX
CHUNK_TOKENIZER_PATH RERANKER_BASE_URL RERANKER_MODEL'''.split(), '')
DEFAULTS.update(LLM_CONTEXT_TOKENS='16384', LLM_LIGHT_CONTEXT_TOKENS='16384', WIKI_OUTPUT_TOKENS='2048',
    LLM_TIMEOUT_SECONDS='300', LLM_QUEUE_TIMEOUT_SECONDS='300', LLM_MAX_CONCURRENT_REQUESTS='1',
    RESEARCH_TIMEOUT_SECONDS='1500', RERANKER_TIMEOUT_SECONDS='30', EMBEDDING_TIMEOUT_SECONDS='30',
    EMBEDDING_MAX_INPUT_TOKENS='512', EMBEDDING_BATCH_SIZE='1', EMBEDDING_API_FORMAT='company',
    EMBEDDING_AUTH_HEADER='Authorization', EMBEDDING_AUTH_SCHEME='Bearer', CHUNKING_MODE='auto',
    CHUNK_MAX_TOKENS='512', CHUNK_OVERLAP_TOKENS='48', CHUNK_PARENT_MAX_TOKENS='2048',
    SEMANTIC_BREAKPOINT_PERCENTILE='90', SEMANTIC_MIN_DISTANCE='0.12', SEMANTIC_BUFFER_SENTENCES='1')
# Every supported role override must be explicit in the isolated environment;
# an omitted key would otherwise fall through to the application's .env file.
DEFAULTS.update({f'LLM_{role}_{field}':'' for role in ('ANSWER','BACKGROUND','REVIEW')
    for field in ('MODEL','CONTEXT_TOKENS','TOKENIZER_PATH','RESOURCE_GROUP','DEPLOYMENT_REVISION','MAX_OUTPUT_TOKENS')})
DEFAULTS.update(dict.fromkeys(('LLM_RESOURCE_GROUP','LLM_DEPLOYMENT_REVISION','LLM_LIGHT_DEPLOYMENT_REVISION',
    'EMBEDDING_RESOURCE_GROUP','EMBEDDING_DEPLOYMENT_REVISION','RERANKER_RESOURCE_GROUP','RERANKER_DEPLOYMENT_REVISION'),''))


def child_environment(env_file, sandbox, *, live, step=None):
    values = read_ai_environment(env_file) if live else {}
    env = dict(os.environ)
    def relevant(key):
        if step in (None,'wiki','answer'):return True
        if step in ('llm','llm-light'):
            return (key.startswith('LLM_') or key=='WIKI_OUTPUT_TOKENS') and (step=='llm-light' or not key.startswith('LLM_LIGHT_'))
        if step in ('embedding','chunking'):return key.startswith(('EMBEDDING_','CHUNK','SEMANTIC_'))
        if step=='reranker':return key.startswith('RERANKER_') or key in ('EMBEDDING_BASE_URL','EMBEDDING_API_KEY')
        return False
    env.update({key: str(values.get(key) if relevant(key) and values.get(key) is not None else default) for key, default in DEFAULTS.items()})
    env.update(EVIDENCE_DB_PATH=str(sandbox/'diagnostic.sqlite3'), AUTH_MODE='local_admin',
        BOOTSTRAP_ADMIN_EMPLOYEE_ID='0000000', SSO_COOKIE_NAME='', PYTHONDONTWRITEBYTECODE='1',
        PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1', TEMP=str(sandbox), TMP=str(sandbox), TMPDIR=str(sandbox))
    return env


def selected_steps(value):
    if value == 'all':return list(STEPS)
    items = list(dict.fromkeys(part.strip() for part in value.split(',') if part.strip()))
    if not items or any(item not in STEPS for item in items):
        raise ValueError('알 수 없는 단계입니다. --list로 단계 이름을 확인하세요.')
    return items


def output_directory(path):
    path = Path(path).resolve()
    protected = [ROOT/name for name in ('data','wiki','raw','.git','backend','src','dist','tests','shared')]
    configured_db = os.environ.get('EVIDENCE_DB_PATH')
    if configured_db:
        protected.extend([Path(configured_db).resolve(),Path(configured_db).resolve().parent/'wiki'])
    if any(path.is_relative_to(p.resolve()) for p in protected):
        raise ValueError('보고서는 운영 자료·소스 폴더 밖에 저장하세요. 기본 artifacts/diagnostics를 권장합니다.')
    return path


def run_checks(steps, *, live=False, env_file=None, output=None, stage_timeout=3600):
    if not steps or any(s not in STEPS for s in steps):raise ValueError('Invalid diagnostic steps')
    env_file = Path(env_file or ROOT/'.env').resolve()
    output = output_directory(output or ROOT/'artifacts'/'diagnostics')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    directory = output/run_id
    directory.mkdir(parents=True, exist_ok=False)
    report = dict(version=1, run_id=run_id, created_at=datetime.now(timezone.utc).isoformat(), live=live, results=[])
    for step in steps:
        title = STEPS[step][0]
        result = dict(step=step, title=title, status='running', mode='실제 API' if live and step!='unit' else '로컬', checks=[], calls=[])
        report['results'].append(result)
        write_report(directory, report)
        print(f'[{step}] {title} ...', flush=True)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='evidence-check-') as temporary:
            sandbox = Path(temporary).resolve()
            # Even an unusual OS temp setting may not put fixtures in production.
            if any(sandbox.is_relative_to((ROOT/p).resolve()) for p in ('data','wiki','raw')):
                raise ValueError('운영 자료 밖의 OS 임시 디렉터리를 설정하세요.')
            stage_live = live and step not in ('unit','ingest','curation')
            env = child_environment(env_file, sandbox, live=stage_live, step=step)
            command = [sys.executable, '-B', '-m', 'backend.diagnostics', '--_worker', step, '--_sandbox', str(sandbox)]
            if stage_live:command.append('--live')
            process = None
            interrupted = False
            summary = ''
            try:
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                process.wait(timeout=stage_timeout)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
                summary = '단계 전체 시간 한도를 초과했습니다. 확인된 호출만 기록합니다. 서버 측 연산은 계속될 수 있습니다.'
            except KeyboardInterrupt:
                if process and process.poll() is None:process.kill(); process.wait()
                summary = '사용자가 점검을 중지했습니다. 서버 측 연산은 계속될 수 있습니다.'
                interrupted = True
            except OSError:
                summary = '점검 프로세스를 시작하지 못했습니다. Python 실행 권한을 확인하세요.'
            saved = sandbox/'result.json'
            if saved.is_file():
                try:result.update(json.loads(saved.read_text(encoding='utf-8')))
                except (ValueError, OSError):summary = summary or '점검 결과를 읽지 못했습니다.'
            if summary or result['status']=='running' or process and process.returncode not in (0, 1):
                result.update(status='failed', summary=summary or '점검 프로세스가 정상 종료되지 않았습니다. 설치된 의존성과 실행 권한을 확인하세요.')
                for call in result['calls']:
                    if call['status']=='running':
                        call['status']='interrupted'
                        call['elapsed_ms']=max(0,round((time.time()-call.get('started_at',time.time()))*1000))
            result['elapsed_ms'] = round((time.monotonic()-started)*1000)
        write_report(directory, report)
        print(f"[{step}] {result['status']} ({result['elapsed_ms']/1000:.1f}s)", flush=True)
        if interrupted:
            for remaining in steps[steps.index(step)+1:]:
                report['results'].append(dict(step=remaining,title=STEPS[remaining][0],status='skipped',mode='미실행',summary='앞선 단계에서 사용자가 중지했습니다.',checks=[],calls=[],elapsed_ms=0))
            write_report(directory, report)
            break
    print('HTML: '+str(directory/'report.html'), flush=True)
    print('JSON: '+str(directory/'report.json'), flush=True)
    failed = any(r['status']=='failed' for r in report['results'])
    incomplete = any(r['status']=='skipped' for r in report['results'])
    return directory, 1 if failed else 2 if incomplete else 0


def worker(step, sandbox, live):
    # This branch deliberately imports no application module until isolation is set.
    sandbox = Path(sandbox).resolve()
    if not sandbox.is_dir() or Path(os.environ.get('EVIDENCE_DB_PATH','')).resolve() != sandbox/'diagnostic.sqlite3':
        raise ValueError('Worker isolation was not initialized by the diagnostic runner')
    from .diagnostic_safety import install_guard
    install_guard(sandbox, live=live)
    from .diagnostic_steps import execute
    return execute(step, sandbox, live)


def main():
    parser = argparse.ArgumentParser(description='Evidence Studio 단계별 점검 · 운영 DB/Wiki와 분리')
    parser.add_argument('--list', action='store_true', help='단계 이름과 점검 범위 표시')
    parser.add_argument('--steps', default='unit,ingest,curation,chunking', help='쉼표로 단계 선택 또는 all')
    parser.add_argument('--live', action='store_true', help='선택 단계에서 실제 사내 API 사용 허용 (토큰 소모)')
    parser.add_argument('--env-file', type=Path, help='기본: 프로젝트 .env. 기존 프로세스 환경변수가 우선')
    parser.add_argument('--output', type=Path, help='보고서 상위 디렉터리 (기본 artifacts/diagnostics)')
    parser.add_argument('--stage-timeout', type=int, default=3600, help='단계별 전체 시간 한도, 초 (기본 3600)')
    parser.add_argument('--retry-from', type=Path, help='이전 report.json에서 실패한 단계만 선택. 실제 API에는 --live를 다시 지정')
    parser.add_argument('--_worker', choices=STEPS, help=argparse.SUPPRESS)
    parser.add_argument('--_sandbox', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._worker:return worker(args._worker,args._sandbox,args.live)
    if args.list:
        for key,(title,detail) in STEPS.items():print(f'{key:12} {title} · {detail}')
        return 0
    try:
        if not 1 <= args.stage_timeout <= 14400:raise ValueError('--stage-timeout은 1~14400초입니다.')
        steps = selected_steps(args.steps)
        if args.retry_from:
            previous = json.loads(args.retry_from.read_text(encoding='utf-8'))
            steps = list(dict.fromkeys(r['step'] for r in previous['results'] if r['status']=='failed'))
            if any(s not in STEPS for s in steps):raise ValueError('지원하지 않는 단계가 있는 보고서입니다.')
            if not steps:
                print('이 보고서에는 재실행할 실패 단계가 없습니다.');return 0
        _, code = run_checks(steps, live=args.live, env_file=args.env_file, output=args.output, stage_timeout=args.stage_timeout)
        return code
    except (ValueError, OSError, KeyError, TypeError):
        print('점검 설정을 확인하세요. 단계 이름은 --list, 경로·옵션은 --help에서 확인할 수 있습니다.',file=sys.stderr)
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
