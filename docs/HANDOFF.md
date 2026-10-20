# Evidence Studio 사내 이관 안내

기준: 2026-10-20. 수요 기반 구축과 비동기 질문을 최신 대화·보고서 기능에 통합했습니다. [처리량 구조](THROUGHPUT_ARCHITECTURE.md)에 작업 우선순위와 공개 범위를 설명합니다. 공개 이관 패키지의 초기 데이터 설명과 기존 설치의 데이터 보존을 구분하세요.

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

실제 `.env`, `.git`, `.venv`, `node_modules`, 기존 분석 JSON·DB/Wiki 백업, 테스트용 DB 복사본, 캐시·로그·스크린샷은 포함하지 않습니다. `shared`에는 실제 앱의 분류·용어 초기값과 검색 평가 입력 형식만 둡니다. 회귀 테스트는 임시 PDF·DB로 실행하며 실제 라이브러리에 예제 논문을 적재하지 않습니다.

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

`raw/` 스캔은 시작 즉시, 이후 **60초마다** 수행됩니다. 안정된 파일을 처리 큐에 넣고 원문·위치·로컬 검색 색인을 적재합니다. 이 단계는 LLM·Embedding·Wiki를 기다리지 않습니다. 전문가 담당 논문과 질문 수요가 있을 때 배경 분석을 예약하며 같은 원문 구간은 여러 전문가가 공유합니다. 파일 해시로 중복을 확인합니다. 자료가 들어오면 수요에 따라 모델 호출과 새 데이터 생성이 시작될 수 있습니다. 하위 폴더는 감시하지 않습니다.

`dist/`가 있어 Node 없이 사용할 수 있습니다. UI 수정 시 Node.js 22.12 이상, pnpm 11.25.0으로 `pnpm install --frozen-lockfile`, `pnpm build`를 실행합니다.

## 3. 사내 모델 설정 계약

| 설정 | 입력 / 현재 계약 |
|---|---|
| `LLM_BASE_URL`, `LLM_BASE_MODEL`, `LLM_API_KEY` | Chat Completions. `messages` 전송, `choices[0].message.content` 응답. 모델은 프롬프트가 지정한 JSON 객체를 반환해야 함 |
| `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, `EMBEDDING_API_KEY` | `/embedding`을 포함한 전체 URL. Bearer 인증, `{input: text, model: model}`, `data[0].embedding`. 현재 한 문자열씩 전송 |
| `RERANKER_MODEL` | `EMBEDDING_BASE_URL`과 `EMBEDDING_API_KEY` 재사용. 동일한 전체 URL에 POST. `{model, query, documents}` → `results[{index,relevance_score}]` 또는 `[{index,score}]`. 실제 사내 계약과 비교 필요 |
| `EMBEDDING_MAX_INPUT_TOKENS` | 사내 모델의 입력 한도. 예시값 512를 실제 모델에 맞게 확인 |
| `CHUNK_TOKENIZER_PATH` | 선택. 사내 모델의 로컬 `tokenizer.json`이 있으면 지정. 빈 값이면 비교용 tokenizer 사용; 실제 모델과 token 수가 다를 수 있음 |

API 키는 backend만 읽습니다. 실제 `.env`는 버전 관리/소개 HTML에 포함하지 않습니다. 프로세스 환경변수가 `.env`보다 우선합니다. 설정 변경 후 재시작합니다. 자동으로 외부 공개 모델을 호출하지 않습니다.

Embedding 연결 검사(짧은 테스트 문장을 실제 사내 API에 전송):

```powershell
.\.venv\Scripts\python.exe -m backend.reindex_search --check
```

원문 검색은 수집 직후 가능합니다. 의미 기반 검색이 필요하면 모델 설정 후 `python -m backend.reindex_search`를 명시적으로 실행합니다. 재색인 CLI는 기존 DB가 있으면 백업을 생성합니다. 전체 Wiki 재구축은 검색 활성화의 조건이 아닙니다. 기존 DB는 시작 시 빠진 로컬 QA 색인과 활성 전문가 수요를 모델 호출 없이 보충합니다.

## 4. 재구축 경로와 현재 한계

```text
raw PDF → pypdf text 추출·위치·분류 → 로컬 원문 검색 → 질문의 초기 근거
                  └→ 전문가/질문 수요 → 구간별 분석·독립 검토 → 공통 원천 Wiki
                                          └→ 주제 자료·전문가 특화 지식
질문 → 서버 작업 ID → 우선순위 큐 → 상세 답변·인용 검토 → 동일 화면 후속 결과
```

| 기능 | 현재 상태 / 사내에서 필요한 작업 |
|---|---|
| PDF 수집·분류·문맥 분할 | 즉시 로컬 구조 분할·FTS. 의미 분할과 벡터 재색인은 명시적 작업으로 분리. 분류는 규칙 기반 |
| 공통·전문가 Wiki | 전문가 수요에 따라 점진 구축. 공통 구간 분석·검증 결과 공유, 입력 hash별 재개. 기존 수동 갱신과 Markdown 이력 유지 |
| 검색·답변 | 초기 로컬 근거, 검증된 동일 질문 캐시, 준비 자료가 있으면 답변·검토 2회. 새 조사는 계획·보완 추가. 기존 동기·스트림 API 호환 |
| **claim·주요 수치·실험 조건 자동 추출** | 원문 위치와 함께 분석 후보를 저장하고 별도 검토. canonical 원장 자동 승격은 하지 않음. 기존 `backend/pilot_curate.py`의 검증 JSON 가져오기 유지 |
| 한국어 초록 요약 자동 생성 | `backend/abstract_summaries.py`는 원문 fingerprint 검증 importer. 프롬프트는 있으나 새 PDF마다 호출·저장하는 작업 추가 필요 |
| OCR / 표·그림 추출 | pypdf text 추출과 검토 UI 구현. 스캔 PDF/표·그림의 OCR 자동 연결 추가 필요 |
| SSO·유저 관리 | 권한·유저 DB·자동 등록·개인 페이지 구현. 쿠키 검증/프로필 조회 두 어댑터 연결 필요 |
| 팀 질문·개인 기록 | 새 요청의 소유권·공유 상태·취소·이력은 서버 저장. 브라우저 대화 묶음·북마크 동기화와 보존/삭제 정책은 별도 운영 범위 |
| 웹 PDF 업로드 | 현재 실제 앱의 업로드 버튼은 비활성. `raw/` 투입 경로 사용 |

**LLM 설정만으로 삭제한 claim·수치·조건이 자동 복원되지는 않습니다.** 구조화 근거가 없어도 준비된 원문으로 Wiki를 작성할 수 있지만, 이를 canonical claim DB를 완성한 상태로 간주하면 안 됩니다.

구간별 추출 후보는 인용·수치·일부 명시적 조건 검사와 독립 모델 검토를 거칩니다. 자동 검토를 사람 검토 완료나 canonical 원장 완성으로 간주하지 않습니다. 제목·초록 요약 및 분류 보완은 별도 범위입니다.

공통 지침 원본은 `backend/prompts/wiki_policy.md`입니다. ingest·답변·lint가 함께 읽습니다. 공통 Wiki를 먼저 재사용하고 전문가 담당 범위를 지키며, 전용 주제만 추가합니다. `wiki/index.md`, `log.md`, `.history/`는 시스템이 관리합니다. 직접 편집 시 lint/adopt 절차는 [Markdown Wiki 안내](MARKDOWN_WIKI.md)를 참고하세요.

## 5. 사용자 및 운영 연결

`backend/sso_adapter.py`의 `verify_sso_cookie`와 `fetch_employee_profile`을 사내 계약대로 구현한 후 `AUTH_MODE=sso`, `SSO_COOKIE_NAME`을 설정합니다. 검증된 새 사번은 이름·팀과 함께 일반 유저로 자동 등록됩니다. 별도 회원가입은 없습니다. 첫 실행 전에 실제 초기 관리자 사번을 `.env`의 `BOOTSTRAP_ADMIN_EMPLOYEE_ID`로 지정합니다. 재시작과 SSO 프로필 동기화는 기존 권한을 덮어쓰지 않습니다. SSO 실패 시 임시 관리자로 우회하지 않습니다.

| 역할 | 권한 |
|---|---|
| 일반 유저 | 질문·논문·Wiki 열람, 자료/용어 수정 제안 |
| 슈퍼 유저 | 일반 기능 + 전문가 관리·지식 관리 |
| 관리자 | 슈퍼 유저 기능 + 유저 관리·수동 Wiki 갱신 |

새 질문의 이력·공개 범위·진행 상태는 서버에 소유자별로 저장합니다. 이전 대화나 보고서 문맥을 사용하는 질문은 비공개로 제한합니다. 대화 묶음·휴지통·북마크는 브라우저 저장이며 브라우저 대화 삭제가 서버 요청이나 실행 snapshot 삭제를 뜻하지 않습니다. 팀 공개 전 SSO와 접근/삭제/보존 정책을 적용하세요. [사용자 설계](USER_ACCESS.md), [답변 구조](RESEARCH_QA.md) 참고.

SQLite·앱 내부 워커의 단일 프로세스 구성입니다. 팀 규모별 처리량/검색 품질/지연은 사내 모델과 실제 질문으로 평가해야 합니다. 이 저장소는 DB·Wiki가 없는 개발 이관본이며 사내 운영 검증을 대신하지 않습니다.

## 6. 검증과 문서

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.mjs
```

실제 연결 후에는 PDF 1편 → 자료 현황 → 논문/근거 → 공통/전문가 Wiki → 질문/원문 인용 순으로 먼저 확인하세요. 읽기 실패 PDF, 근거가 부족한 질문, 조건이 다른 수치 비교도 점검 대상입니다. 검색 평가 도구는 [RESEARCH_EVALUATION.md](RESEARCH_EVALUATION.md)에 있습니다.

구조는 [DATABASE_PLAN.md](DATABASE_PLAN.md), [MARKDOWN_WIKI.md](MARKDOWN_WIKI.md), [RESEARCH_QA.md](RESEARCH_QA.md), [TERMINOLOGY.md](TERMINOLOGY.md)를 참고합니다. 설계 문서의 미래 계획을 구현 완료로 해석하지 않습니다. 공개본의 DB·Wiki·raw는 비어 있으며, 사내에서 넣은 논문으로 데이터를 구성합니다.


## 7. DB 쓰기 잠금과 작업 복구

문맥 분할·Embedding 계산은 트랜잭션 밖에서 수행합니다. 새 벡터는 메모리에 모은 뒤 원문·검색 인덱스와 함께 저장합니다. 원문 검토/제목 수정과 전체 검색 재구성도 계산을 먼저 끝내며, 저장 전에 입력이 바뀌었으면 변경을 거부합니다. 실패한 계산은 기존 인덱스를 지우지 않습니다.

Wiki LLM 호출도 DB 트랜잭션 밖에서 수행합니다. Wiki 저장·작업 상태·lease의 쓰기 잠금 확보는 최대 3회, 각 5초 대기와 짧은 backoff로 재시도합니다. 성공한 요청 본문이나 LLM 호출을 이 재시도에서 다시 실행하지 않습니다. Wiki worker는 긴 호출 중에도 30초 간격으로 10분 lease를 연장합니다. 일시적인 SQLITE_BUSY/SQLITE_LOCKED는 작업을 대기로 돌려 자동 재시도하며, 이미 발행한 Wiki는 입력 fingerprint가 같으면 재사용합니다. 잠금 때문에 완료 상태 저장만 실패한 ingest는 상태 저장부터 다시 시도합니다.

기존 설치 적용 순서:

1. 실행 중인 앱 서버를 중지하고 data/와 wiki/를 백업합니다. SQLite 백업 시 해당 폴더의 WAL/SHM 파일도 함께 보존하거나 SQLite backup API를 사용합니다.
2. 사내 코드 변경을 보존한 뒤 git pull --ff-only로 갱신하고 서버를 다시 시작합니다. 시작 시 새 테이블과 빠진 로컬 검색 색인·전문가 수요를 추가합니다. 기존 DB 삭제·전체 논문 재분석이 필요하지 않습니다.
3. 대기/중단 상태의 작업은 자동 재개됩니다. 비정상 종료 때 남은 Wiki lease는 만료 후 회수하므로 최대 약 10분 기다릴 수 있습니다. 이전 버전에서 이미 실패로 기록된 Wiki는 전문가 설정의 공통 Wiki 또는 해당 전문가 갱신 버튼으로 다시 요청합니다.
4. 이전 버전에서 PDF 오류로 잘못 기록된 ingest는 자동으로 판별하지 않습니다. 해당 실패 기록과 실제 논문 등록 여부를 따로 확인하세요. 실패한 PDF 전체를 무조건 재분석하지는 않습니다.

SQLite의 쓰기는 여전히 한 번에 하나씩 처리됩니다. 서버는 기존 안내대로 단일 프로세스로 실행하고 전체 코퍼스 reindex CLI는 서버를 중지한 점검 시간에 실행하세요. 이 수정은 긴 모델 대기로 인한 쓰기 잠금 점유와 일시적 경쟁의 실패 처리를 개선하며, 사내 모델의 실제 지연·스토리지 환경은 사내에서 추가 확인해야 합니다.


## 8. 느린 사내 LLM과 timeout 설정

LLM 대기는 코드의 고정 90초에서 기본 300초로 변경했습니다. 기존 .env에 설정이 없어도 새 기본값이 적용됩니다. 값을 바꾸려면 기존 .env에 아래 항목을 추가하고 앱 서버를 재시작하세요. 키가 있는 .env를 .env.example로 덮어쓰지 않습니다.

~~~dotenv
LLM_TIMEOUT_SECONDS=300
LLM_MAX_CONCURRENT_REQUESTS=1
LLM_QUEUE_TIMEOUT_SECONDS=300
RESEARCH_TIMEOUT_SECONDS=1500
~~~

| 설정 | 의미 | 기본값 |
|---|---|---|
| LLM_TIMEOUT_SECONDS | LLM 한 번 호출의 socket I/O 대기 한도 | 300초 |
| LLM_MAX_CONCURRENT_REQUESTS | 전체 프로세스 상한. 자원 그룹마다 한 슬롯도 함께 적용 | 1 |
| LLM_QUEUE_TIMEOUT_SECONDS | 우선순위 LLM 호출 슬롯 대기 한도; 대기 중 취소 가능 | 300초 |
| RESEARCH_TIMEOUT_SECONDS | 심층 질문의 새 작업 시작·LLM 결과 채택 예산; 호출 대기도 포함 | 1,500초 |
| EMBEDDING_TIMEOUT_SECONDS | LLM과 별도인 embedding I/O 대기 한도 | 30초 |
| RERANKER_TIMEOUT_SECONDS | LLM과 별도인 reranker I/O 대기 한도 | 30초 |

시간 설정은 1~1,800초, 질문 예산은 1~7,200초, 전체 동시 호출 상한은 1~8을 허용합니다. 환경변수가 .env보다 우선합니다. 하드웨어 분리가 확인된 자원 그룹을 구성할 때만 동시 상한을 늘립니다. 대기 시간을 늘리는 것은 허용 대기를 늘리는 것이며 처리 속도를 높이는 것은 아닙니다.

이번 점검에서 함께 보완한 부분:

- 질문은 초기 로컬 근거를 먼저 표시합니다. 유효한 동일 질문 캐시는 모델 호출 없이 재사용하고 준비 자료가 있으면 답변·검토 2회, 새 조사는 최대 4회 호출합니다. timeout/혼잡 오류를 받으면 후속 LLM 요청을 중단하고 확인 가능한 근거를 제공합니다.
- Wiki 작업의 모델 timeout/혼잡 오류 발생 시 현재 작업은 명확한 사유로 실패를 기록하고, 다른 범위의 작업은 대기에 남깁니다. 백그라운드 워커는 60초 쉬고 다음 대기 작업을 확인합니다. 실패한 생성 요청 자체를 자동 반복하지 않습니다. 모델 서버가 여전히 처리하고 있을 가능성이 있기 때문입니다.
- 정기 lint도 공통 큐에서 가장 낮은 우선순위로 한 호출씩 실행합니다. 긴 lint는 성공한 구간마다 진행 위치를 저장하므로 다음 구간의 timeout이 앞 구간의 재분석으로 이어지지 않습니다.
- embedding도 socket timeout/HTTP 504 뒤 동일 요청을 자동 재전송하지 않습니다. 그 밖의 일부 일시적 연결 오류·HTTP 429/502/503은 기존처럼 최대 3회 시도합니다. reranker 실패 시에는 기존 keyword/vector 순위와 안내를 사용합니다.
- LLM timeout, 호출 슬롯 대기 초과, HTTP 429/503 혼잡, HTTP 408/504 서버 timeout, 잘못된 JSON/불완전한 응답을 구분합니다. 불완전한 JSON이나 검증되지 않은 내용을 기존 Wiki에 덮어쓰지 않습니다.
- 앞서 수정한 모델 호출 중 DB 쓰기 잠금 해제와 Wiki lease의 30초 주기 연장을 유지합니다. 시간/동시 호출 설정만 변경해도 Wiki 입력 fingerprint는 달라지지 않으므로 기존 내용을 강제로 재생성하지 않습니다.

사내에서 확인할 남은 사항:

| 상황 | 해석과 확인할 점 |
|---|---|
| 앱을 300초로 바꿔도 정확히 60/90초쯤 HTTP 504 | LLM 서버·사내 API gateway·reverse proxy의 별도 timeout을 확인해야 합니다. 앱 설정이 상위 서비스의 제한을 늘리지는 않습니다. |
| 서버 중지/브라우저 답변 중지 후에도 모델이 일함 | 대기열에서는 바로 취소하지만 이미 전송한 HTTP 요청은 반환/timeout까지 계속될 수 있습니다. 제공자 연산까지 취소하려면 사내 API의 취소 지원이 필요합니다. 해당 호출의 slot을 먼저 반환하지 않도록 했습니다. |
| Wiki job 전체가 수십 분 이상 걸림 | 300초는 Wiki job 전체 제한이 아닙니다. 원문 구간 분석과 여러 단계의 종합 각각에 모델 호출이 필요합니다. 짧은 논문은 원천 Wiki를 한 번에 만들고, 긴 논문·큰 주제만 모델 입력 한도에 맞춰 나눕니다(아래 9절). 입력 변경이 없는 부분 결과는 재사용합니다. |
| 긴 논문/큰 주제에서만 timeout 또는 context 초과 | LLM_CONTEXT_TOKENS와 선택한 모델의 tokenizer를 확인하세요. tokenizer 미설정 시 UTF-8 byte 기준의 보수적 추정이며 실측 token이 아닙니다. 기존 Wiki 하나가 입력 한도보다 크면 조용히 자르지 않고 오류를 남깁니다. |
| 응답은 왔으나 JSON·근거 검증 실패 | 속도와 별개의 출력 품질/형식 문제입니다. Chat Completions 응답 구조, JSON 작성 능력, finish_reason, 원문 인용을 확인하세요. 검증 기준을 낮춰 통과시키지 않습니다. |

urllib의 timeout은 각 blocking operation에 적용되며 요청 전체에 대한 강제 종료 시계가 아닙니다. 질문 예산 초과 후 도착한 LLM 결과는 버리지만, 이미 진행 중인 I/O가 즉시 종료되는 것은 아닙니다. [Python urllib 문서](https://docs.python.org/3.12/library/urllib.request.html#urllib.request.urlopen)

앱의 ask-stream은 heartbeat와 X-Accel-Buffering: no를 보냅니다. 앞단 프록시가 buffering을 강제하거나 다른 idle timeout을 적용하면 별도 조정이 필요합니다. nginx의 proxy_read_timeout도 전체 응답 시간이 아니라 읽기 사이 대기 제한입니다. 사내 LLM 게이트웨이 설정은 이 저장소에서 변경할 수 없습니다. [nginx proxy 문서](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_read_timeout)

검증은 로컬 지연 HTTP 서버와 가상 시간·모델을 사용했습니다. 사내 API 키/논문을 외부로 전송하지 않았습니다. 사내에서는 소량의 논문으로 호출별 실제 시간, timeout/HTTP 상태, 대기열 길이를 관찰해 조정하세요. 질문별 실행 기록의 trace.model_calls와 trace.model_timings에 소요 시간이 저장됩니다. 기존 Wiki 실패는 설정 적용 후 해당 범위 갱신을 다시 요청하며, DB·Wiki 삭제나 전체 재구축은 필요하지 않습니다.

## 9. 논문 길이에 따른 Wiki 구성과 경량 모델

이 절은 관리자가 명시적으로 요청하는 기존 수동 Wiki 갱신 경로의 입력 분할과 경량 모델을 설명합니다. 자동 구축은 [수요 기반 구간 분석·독립 검토](THROUGHPUT_ARCHITECTURE.md)를 사용하며 `LLM_BACKGROUND_MODEL`·`LLM_REVIEW_MODEL`로 역할을 나눕니다. 수동 갱신도 공통 큐를 거치므로 사용자 답변과 무제한으로 경쟁하지 않습니다.

짧다는 기준은 페이지 수가 아니라 **작성 지침 + 원문 + 근거 metadata + 필요한 이전 Wiki + 출력 여유가 선택한 모델의 context에 들어가는지**입니다.

- 원천 claim·수치·조건이 이미 있으면 기존 DB 재사용 경로를 유지합니다.
- 원문 전체가 들어가면 여러 페이지를 한 요청에 넣고 곧바로 논문 원천 Wiki를 작성합니다. 별도의 페이지별 Wiki나 논문 요약 재종합 호출은 없습니다. 공통 주제 종합 호출은 별도입니다.
- 전체가 들어가지 않으면 문단·문장 경계를 우선하는 구조 기반 분할로 인접 페이지의 내용을 묶습니다. 한 문장 자체가 한도보다 크면 더 작은 단위로 나눕니다. 모든 ready 페이지의 원문은 빠짐없이 입력에 포함하며, 페이지 ID·document ID·원문 offset을 별도로 보존합니다. 검색용 embedding 기반 semantic chunking은 그대로 유지하고 Wiki 분할을 위해 embedding을 재호출하지 않습니다.
- 조각 분석 결과가 한 번에 들어가면 바로 논문 Wiki로 종합합니다. 공통 주제도 같은 방식으로 처리합니다. 고정 6개 규칙을 없애고 실제 요청 크기가 넘을 때만 중간 종합을 추가합니다. 단일 문서를 그대로 재요약하는 중간 호출은 생략합니다.
- 원문 분석과 내부 중간 종합은 자신의 이전 요약을 반복해서 보내지 않습니다. 최종 주제·논문 종합에는 기존 Wiki를 포함해 편집 내용을 반영합니다. 직접 수정한 미승인 Markdown의 덮어쓰기 차단은 유지합니다.
- 원문 인용은 응답 검증 시 해당 페이지의 원래 문자열·offset과 대조합니다. 전달하지 않은 evidence ID, 틀린 인용, 잘린 JSON은 발행하지 않습니다. 모든 원문이 입력에 포함되었다는 것과 모든 과학적 주장을 추출·검증했다는 것은 다릅니다.
- 변경 없는 기존 Wiki와 완료된 중간 문서는 재사용합니다. 이전 버전의 유효한 페이지별 Wiki tree도 새 코드 적용만으로 강제 재구축하지 않습니다. 새 논문이나 실제 근거·지침 변경부터 필요한 범위에 새 구성을 적용합니다. DB migration이나 검색 재색인은 필요하지 않습니다.

### 모델 역할과 설정

| 작업 | 모델 |
|---|---|
| 짧은 논문 원문 → 원천 Wiki, 긴 논문 원문 묶음 → 사실·인용 | LLM_LIGHT_MODEL 설정 시 경량 모델, 없으면 기본 모델 |
| 전체 논문이 기본 모델에는 들어가지만 경량 모델에는 안 들어감 | 분할 대신 기본 모델 1회 |
| 경량 모델이 지침·최소 근거도 수용하지 못함 | 전송 전에 기본 모델로 분할 계획 |
| 여러 원천 지식 종합·공통 주제·전문가 특화 노트 | LLM_BASE_MODEL |
| 질문 계획·답변·검증, 내용 lint | LLM_BASE_MODEL 유지 |
| 목차·파일·링크·구조 검사 | 코드 실행, LLM 호출 없음 |

경량 모델의 과학적 정확도를 자동으로 보증하지 않습니다. 관리자가 비교 검증한 모델을 선택적으로 지정하는 방식입니다. 인용 검증은 원문 연결을 확인하며, 조건 해석의 타당성까지 보증하지 않습니다. 경량 모델의 timeout/잘못된 응답 뒤 기본 모델로 자동 재호출하지 않습니다. 기존 Wiki를 보존하고 문제를 표시합니다.

기존 .env를 덮어쓰지 말고 필요한 항목만 추가하세요.

~~~dotenv
# 기본 모델의 실제 입력+출력 context 규격
LLM_CONTEXT_TOKENS=16384
LLM_TOKENIZER_PATH=
WIKI_OUTPUT_TOKENS=2048

# 경량 모델을 사용하지 않으려면 비워 둡니다.
LLM_LIGHT_MODEL=
LLM_LIGHT_CONTEXT_TOKENS=16384
LLM_LIGHT_TOKENIZER_PATH=
~~~

LLM_BASE_MODEL과 LLM_LIGHT_MODEL은 LLM_BASE_URL·LLM_API_KEY를 공유합니다. 경량 모델을 쓰려면 모델명과 실제 context 한도만 지정합니다. 두 모델은 기존 LLM_TIMEOUT_SECONDS와 동시 호출 제한도 공유합니다. URL·키 통합, 기존 .env 변경 방법, 선택적 튜닝은 [설정 안내](CONFIGURATION.md)를 참고하세요.

context는 모델별 4,096~1,048,576, Wiki 출력은 256~16,384 token 설정을 허용하며 기본값은 각각 16,384 / 2,048입니다. 기본값이 사내 모델의 실제 규격이라는 뜻은 아닙니다. 출력과 512 token의 chat template 여유를 뺀 입력 예산으로 전체 직렬화 요청을 검사합니다. 지침 자체가 너무 크면 전송 전에 실패합니다. 모델이 max_tokens를 지원해야 하며 Wiki 요청에 WIKI_OUTPUT_TOKENS를 전달합니다. finish_reason=length 등 불완전한 출력은 기존처럼 거부합니다.

LLM_TOKENIZER_PATH와 LLM_LIGHT_TOKENIZER_PATH에는 해당 모델의 로컬 Hugging Face tokenizer.json을 선택적으로 지정합니다. 자동 다운로드나 외부 서비스 호출은 없으며 truncation/padding을 비활성화해 입력을 작게 세지 않도록 합니다. embedding용 CHUNK_TOKENIZER_PATH와 별개입니다. 경로를 비워두면 UTF-8 byte 수를 보수적인 token 추정치로 사용해 한국어·수식도 여유 있게 계산합니다. 이 추정은 실제 사내 tokenizer/chat template와 정확히 일치하지 않으며 작은 모델에서 더 많은 조각을 만들 수 있습니다. 정확한 파일과 API usage를 확보해 조정하세요. 분할은 [semantic-text-splitter의 문단·문장 경계](https://semantic-text-splitter.readthedocs.io/en/stable/semantic_text_splitter.html#textsplitter)를 사용합니다.

검증은 임시 DB와 결정적 가상 모델로 수행했습니다. 10쪽의 짧은 합성 논문은 기존 원천 경로 13회 대신 1회 호출로 처리했으며, 긴 합성 논문의 모든 원문 문자와 페이지별 인용 위치가 보존되는지 검사합니다. 실제 논문 token 절감률이나 경량 모델의 정확도를 측정한 결과는 아닙니다. Wiki job별 실측 사용량은 아래 10절의 작업 현황에서 확인합니다.


## 10. Wiki 작업별 모델 사용량

왼쪽 **작업 현황 → Wiki 작업 내역**에서 생성·갱신 job별 사용량을 확인합니다. 기본 3개를 표시하고 펼치면 진행 중·대기 작업을 우선한 최근 최대 12개를 볼 수 있습니다. 화면이 열려 있는 동안 5초마다 갱신하며 완료 후와 앱 재시작 후에도 기록을 유지합니다.

- 실제 사용 모델명과 기본/경량 역할, 호출 횟수, 입력·출력·전체 token, 작업 실행 시간을 표시합니다.
- **모델별 사용량**을 펼치면 원천 Wiki·원문 묶음·공통 주제·중간 종합·전문가 특화 중 수행한 단계와 모델별 누적 응답 시간을 확인합니다.
- 호출 횟수는 HTTP 전송을 시도한 횟수입니다. 실패·timeout도 포함하고 아직 전송하지 않은 슬롯 대기/실패는 별도로 표시합니다. 동일 job에 여러 논문/전문가가 연결되어도 사용량을 중복 집계하지 않습니다.
- token은 Chat Completions 응답의 usage.prompt_tokens, completion_tokens, total_tokens입니다. 입력·출력이 있고 전체만 빠졌으면 둘을 더합니다. API 미제공·timeout·진행 중 사용량을 0으로 추정하지 않으며, 일부 응답만 알려졌으면 **일부**와 확인 횟수를 표시합니다. 잘못된 생성 JSON으로 작업이 실패했더라도 API가 반환한 token은 합산합니다.
- 작업 시간은 최초 대기열 체류 시간을 제외하고 실행이 시작된 뒤의 처리·모델 슬롯 대기·응답 시간을 포함합니다. 모델 응답 시간은 각 HTTP 전송 이후 응답/오류까지의 시간을 더한 값입니다. 서버 중단 후 재개한 job은 기록된 실행들을 합산하고, 서버가 꺼져 있던 시간은 제외합니다.
- 변경 없는 Wiki 재사용·목차 갱신 등 실제 호출이 없는 새 작업은 **LLM 호출 없음 / 0회**입니다. 기능 추가 전 작업은 **기록 없음**이며 소급 추정하지 않습니다.
- 이 표는 Wiki 생성·갱신 job의 LLM만 집계합니다. 질문 답변, 별도 정기 lint, embedding·reranker 호출은 포함하지 않습니다. token은 확인 가능한 API 보고치이며, timeout 이후 제공자가 계속 수행한 연산까지 확정하는 과금 명세가 아닙니다.

앱 재시작 시 기존 DB에 wiki_job_runs, wiki_model_calls 테이블을 추가합니다. 논문·Wiki를 삭제하거나 재구축할 필요가 없습니다. 호출 시작/종료 기록은 짧은 별도 트랜잭션으로 저장하고 모델 응답 동안 DB 쓰기 잠금을 잡지 않습니다. 일시적 잠금 경쟁 시 기록만 보류했다가 다음 이벤트나 작업 종료 시 저장하며, 사용량 기록을 위해 모델을 재호출하지 않습니다. 응답 저장 전에 프로세스가 강제 종료된 경우 확인할 수 없는 사용량은 미제공으로 남습니다.

사용량 테이블에는 job/run 식별자, 모델명·역할·단계, 상태, 시각·시간·token만 저장합니다. prompt·논문 본문·답변 본문·API URL·키를 복사하지 않습니다. 읽기 화면은 기존 인증된 사용자에게 제공하고, 갱신 요청 권한은 기존 관리자 제한을 유지합니다.
