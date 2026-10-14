# Evidence Studio 사내 이관 안내

기준: 2026-10-14. 현재 실행 코드 기준이며 이전 목업·시범 운영 문서보다 우선합니다.

## 1. 전달 구성과 초기 상태

| 구성 | 이관 상태 |
|---|---|
| `backend/`, `src/`, `shared/`, `tests/` | 코드·schema·분류/용어 기본값·프롬프트·검증 코드 유지 |
| `dist/` | 현재 React 앱의 빌드 포함 |
| `raw/` | `.gitkeep`만 포함. 사내에서 별도로 준비한 PDF를 넣음 |
| `data/`, `wiki/` | `.gitkeep`만 포함. 기존 DB·Wiki·이력·색인·유저 데이터 없음 |
| `.env.example` | 주소·모델·키가 비어 있는 설정 양식 |
| `docs/evidence-studio-introduction.html` | 단일 파일 팀 소개 자료; 외부 리소스 없음 |
| `PACKAGE_MANIFEST.json` | 전달 파일 목록·SHA-256 및 제외 항목 기록 |

실제 `.env`, `.git`, `.venv`, `node_modules`, 기존 분석 JSON·DB/Wiki 백업, 테스트용 DB 복사본, 캐시·로그·스크린샷은 포함하지 않습니다. `shared`와 테스트의 목업 fixture는 소스의 일부이며 실제 DB로 적재되지 않습니다.

빈 DB 파일을 미리 배포하지 않습니다. 첫 시작 시 `backend/pilot_schema.sql`로 새 DB를 만들고 기본 전문가 9개·기본 용어 사전·`.env`의 `BOOTSTRAP_ADMIN_EMPLOYEE_ID`로 지정한 관리자 계정을 초기화합니다. 이전 전문가 편집·유저 목록·사전 편집·연구 결과는 유지하지 않습니다. Wiki 본문과 목차/이력은 이후 새 작업으로 생성됩니다.

## 2. 설치와 실행

Python 3.12에서 검증했습니다. 프로젝트 루트에서 실행합니다. Python·pip와 사내 패키지 저장소 접근이 필요합니다. 폐쇄망이라면 별도로 승인된 Python 설치본과 대상 OS/Python 버전에 맞는 wheel을 준비해야 합니다. 이 저장소는 의존성 오프라인 설치 묶음이 아닙니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock.txt
Copy-Item .env.example .env
```

1. `.env`에 모델 설정과 `BOOTSTRAP_ADMIN_EMPLOYEE_ID`를 채웁니다. 이미 작성한 `.env`는 덮어쓰지 않습니다.
2. API 계약과 사내 인증서 신뢰 설정을 확인합니다. 인증서 검증을 끄지 않습니다.
3. 아래 명령으로 **단일 프로세스**를 시작합니다. `--workers`/`--reload` 없이 실행합니다.
4. `http://127.0.0.1:8011`에서 자료 현황의 처리·검토 상태를 확인합니다.

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8011
```

초기 관리자 사번을 비우면 `local_admin`에서만 데모 ID `0000000`을 사용합니다. SSO 모드에서는 실제 사번이 필수이며 데모 ID를 허용하지 않습니다. 설정 변경이 기존 계정의 권한을 삭제·복구하는 기능은 아닙니다.

기본 `AUTH_MODE=local_admin`에서는 모든 접속이 동일한 임시 관리자입니다. 이 명령은 로컬 확인용입니다. 팀에 개방하기 전에 SSO를 구현하고 앱 도메인·HTTPS·접근 제어·운영 프로세스를 구성해야 합니다.

`raw/` 스캔은 시작 즉시, 이후 **60초마다** 수행됩니다. 안정된 파일을 처리 큐에 넣고 DB 적재와 Wiki 갱신을 이어서 실행합니다. 파일 해시로 중복을 확인합니다. 원본 PDF는 이 공개 저장소에 포함하지 않습니다. 모델 설정을 먼저 마친 뒤 별도로 준비한 PDF를 `raw/`에 넣습니다. 자료가 들어오면 모델 호출과 새 데이터 생성이 시작될 수 있습니다. 하위 폴더는 감시하지 않습니다.

`dist/`가 있어 Node 없이 사용할 수 있습니다. UI 수정 시 Node.js 22.12 이상, pnpm 11.25.0으로 `pnpm install --frozen-lockfile`, `pnpm build`를 실행합니다.

## 3. 사내 모델 설정 계약

| 설정 | 입력 / 현재 계약 |
|---|---|
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | Chat Completions. `messages` 전송, `choices[0].message.content` 응답. 모델은 프롬프트가 지정한 JSON 객체를 반환해야 함 |
| `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, `EMBEDDING_API_KEY` | `/embedding`을 포함한 전체 URL. Bearer 인증, `{input: text, model: model}`, `data[0].embedding`. 현재 한 문자열씩 전송 |
| `RERANKER_BASE_URL`, `RERANKER_MODEL` | 전체 URL. `EMBEDDING_API_KEY` 재사용. `{model, query, documents}` → `results[{index,relevance_score}]` 또는 `[{index,score}]`. 실제 사내 계약과 비교 필요 |
| `EMBEDDING_MAX_INPUT_TOKENS` | 사내 모델의 입력 한도. 예시값 512를 실제 모델에 맞게 확인 |
| `CHUNK_TOKENIZER_PATH` | 선택. 사내 모델의 로컬 `tokenizer.json`이 있으면 지정. 빈 값이면 비교용 tokenizer 사용; 실제 모델과 token 수가 다를 수 있음 |

API 키는 backend만 읽습니다. 실제 `.env`는 버전 관리/소개 HTML에 포함하지 않습니다. 프로세스 환경변수가 `.env`보다 우선합니다. 설정 변경 후 재시작합니다. 자동으로 외부 공개 모델을 호출하지 않습니다.

Embedding 연결 검사(짧은 테스트 문장을 실제 사내 API에 전송):

```powershell
.\.venv\Scripts\python.exe -m backend.reindex_search --check
```

처음부터 모델 설정 후 수집하는 것이 권장됩니다. 미설정 상태로 수집했다면 설정 후 `python -m backend.reindex_search`로 재색인하고 전문가 설정에서 공통 Wiki와 관련 전문가 Wiki를 갱신합니다. 재색인 CLI는 기존 DB가 있으면 백업을 생성합니다.

## 4. 재구축 경로와 현재 한계

```text
raw PDF → pypdf text 추출·읽기 상태 → 분류 → 문맥 chunking·검색 색인
                  └→ 원문 인용 / (준비된 경우) claim·수치·조건
                      → 공통 Markdown Wiki → 담당 범위의 전문가 Wiki
                      → 질문별 근거 검색 → 심층 답변·인용·근거 검증
```

| 기능 | 현재 상태 / 사내에서 필요한 작업 |
|---|---|
| PDF 수집·분류·문맥 분할 | 구현. Embedding으로 의미 변화 기반 경계를 사용하며 최대 길이도 제한. 분류는 현재 규칙 기반 |
| 공통·전문가 Wiki | 구현. LLM으로 근거를 종합하고 관련 문서 갱신. 변경 없는 입력 재사용. Markdown 본문·DB catalogue·이력 |
| 검색·답변 | 구현. keyword + vector + reranker와 심층 계획/보완/답변/검증. 외부 서비스가 없으면 가능한 근거와 미설정 상태 표시 |
| **claim·주요 수치·실험 조건 자동 추출** | **LLM 추출 워커는 미구현.** `backend/pilot_curate.py`는 원문 인용을 검증하며 JSON을 가져오는 importer. 기존 수동 분석 결과는 이번 이관에서 삭제 |
| 한국어 초록 요약 자동 생성 | `backend/abstract_summaries.py`는 원문 fingerprint 검증 importer. 프롬프트는 있으나 새 PDF마다 호출·저장하는 작업 추가 필요 |
| OCR / 표·그림 추출 | pypdf text 추출과 검토 UI 구현. 스캔 PDF/표·그림의 OCR 자동 연결 추가 필요 |
| SSO·유저 관리 | 권한·유저 DB·자동 등록·개인 페이지 구현. 쿠키 검증/프로필 조회 두 어댑터 연결 필요 |
| 팀 질문·개인 기록 | 화면과 사번별 브라우저 저장. 다중 사용자 대화 동기화와 서버 소유권/삭제 처리 추가 필요 |
| 웹 PDF 업로드 | 현재 실제 앱의 업로드 버튼은 비활성. `raw/` 투입 경로 사용 |

**LLM 설정만으로 삭제한 claim·수치·조건이 자동 복원되지는 않습니다.** 구조화 근거가 없어도 준비된 원문으로 Wiki를 작성할 수 있지만, 이를 canonical claim DB를 완성한 상태로 간주하면 안 됩니다.

사내에서는 문서 단위 추출 워커가 claim/measurement/condition JSON을 만들고 원문 인용·단위·조건을 검증한 뒤 기존 importer/DB에 연결하는 작업을 이어서 구현하세요. 제목·초록 요약 및 분류 보완도 같은 수집 파이프라인에 연결할 수 있습니다. 이번 이관에서 새 추출 시스템을 추가하지 않았습니다.

공통 지침 원본은 `backend/prompts/wiki_policy.md`입니다. ingest·답변·lint가 함께 읽습니다. 공통 Wiki를 먼저 재사용하고 전문가 담당 범위를 지키며, 전용 주제만 추가합니다. `wiki/index.md`, `log.md`, `.history/`는 시스템이 관리합니다. 직접 편집 시 lint/adopt 절차는 [Markdown Wiki 안내](MARKDOWN_WIKI.md)를 참고하세요.

## 5. 사용자 및 운영 연결

`backend/sso_adapter.py`의 `verify_sso_cookie`와 `fetch_employee_profile`을 사내 계약대로 구현한 후 `AUTH_MODE=sso`, `SSO_COOKIE_NAME`을 설정합니다. 검증된 새 사번은 이름·팀과 함께 일반 유저로 자동 등록됩니다. 별도 회원가입은 없습니다. 첫 실행 전에 실제 초기 관리자 사번을 `.env`의 `BOOTSTRAP_ADMIN_EMPLOYEE_ID`로 지정합니다. 재시작과 SSO 프로필 동기화는 기존 권한을 덮어쓰지 않습니다. SSO 실패 시 임시 관리자로 우회하지 않습니다.

| 역할 | 권한 |
|---|---|
| 일반 유저 | 질문·논문·Wiki 열람, 자료/용어 수정 제안 |
| 슈퍼 유저 | 일반 기능 + 전문가 관리·지식 관리 |
| 관리자 | 슈퍼 유저 기능 + 유저 관리·수동 Wiki 갱신 |

대화 목록·공개/비공개·휴지통·북마크는 현재 브라우저 저장입니다. 답변 실행 근거 snapshot은 서버에 소유자별로 저장되지만 대화 동기화와는 별개입니다. 브라우저 대화 삭제가 서버 snapshot 삭제를 뜻하지 않습니다. 팀 공개 전 서버 대화 저장, 접근/삭제/보존 정책을 구현하세요. [사용자 설계](USER_ACCESS.md), [답변 구조](RESEARCH_QA.md) 참고.

SQLite·앱 내부 워커의 단일 프로세스 구성입니다. 팀 규모별 처리량/검색 품질/지연은 사내 모델과 실제 질문으로 평가해야 합니다. 이 저장소는 DB·Wiki가 없는 개발 이관본이며 사내 운영 검증을 대신하지 않습니다.

## 6. 검증과 문서

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.mjs
```

실제 연결 후에는 PDF 1편 → 자료 현황 → 논문/근거 → 공통/전문가 Wiki → 질문/원문 인용 순으로 먼저 확인하세요. 읽기 실패 PDF, 근거가 부족한 질문, 조건이 다른 수치 비교도 점검 대상입니다. 검색 평가 도구는 [RESEARCH_EVALUATION.md](RESEARCH_EVALUATION.md)에 있습니다.

구조는 [DATABASE_PLAN.md](DATABASE_PLAN.md), [MARKDOWN_WIKI.md](MARKDOWN_WIKI.md), [RESEARCH_QA.md](RESEARCH_QA.md), [TERMINOLOGY.md](TERMINOLOGY.md)를 참고합니다. 설계 문서의 미래 계획을 구현 완료로 해석하지 않습니다. 이전 시범의 건수·수동 분석 결과는 새 DB에 포함되지 않습니다.
