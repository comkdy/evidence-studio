# Evidence Studio

GitHub로 받는 경우: [clone·pull 및 실행 안내](docs/GITHUB_TRANSFER.md). 이 공개 저장소에는 앱 소스와 실행 빌드를 포함하며, 원본 PDF·실제 DB·Wiki·API 키는 포함하지 않습니다.

논문을 공통 Wiki와 전문가별 지식으로 연결하고, 질문에 맞는 근거를 찾아 답변하는 연구 지원 앱입니다. React + TypeScript + Vite / FastAPI / SQLite + FTS5 / Markdown Wiki로 구성됩니다.

**2026-10-14 공개 이관본:** 실제 DB·Wiki·기존 분석 JSON·백업·원본 PDF를 제외했습니다. 앱 소스·빌드·DB schema·작성 지침·테스트는 유지합니다. 사내에서 별도로 준비한 PDF를 raw 폴더에 넣어 재구축합니다.

- 팀 소개: [Evidence Studio 소개 HTML](docs/evidence-studio-introduction.html) — 인터넷 연결 없이 브라우저로 열 수 있습니다.
- 설치·연결·재구축: [사내 이관 안내](docs/HANDOFF.md)
- 지식 작성 원칙: [공통 작성 지침](backend/prompts/wiki_policy.md)

## 빠른 시작 — Windows PowerShell

Python 3.12에서 확인했습니다. Git clone 후 프로젝트 폴더에서 실행하세요. Python 패키지는 사내에서 승인된 패키지 저장소 또는 별도 준비한 wheel 파일로 설치합니다. 저장소에 Python 실행 환경과 의존성 패키지 자체는 포함하지 않았습니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock.txt
Copy-Item .env.example .env
```

**서버 실행 전에 `.env`에 사내 LLM·Embedding·Reranker를 설정하세요.** 이미 `.env`를 작성했다면 다시 복사하지 마세요. 실제 키가 들어 있는 `.env`는 저장소에 포함하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8011
```

[http://127.0.0.1:8011](http://127.0.0.1:8011)에서 엽니다. 빌드된 `dist/`를 포함하므로 화면을 수정하지 않는다면 Node 설치가 필요 없습니다. FastAPI 서버를 **한 프로세스**로 실행하세요. `--reload`나 다중 `--workers`는 사용하지 않습니다.

`BOOTSTRAP_ADMIN_EMPLOYEE_ID`가 비어 있으면 로컬 확인용 데모 ID `0000000`을 사용합니다. 사내에서는 실제 초기 관리자 사번을 `.env`에 지정한 뒤 첫 실행하세요. SSO 모드는 실제 사번을 설정해야 시작됩니다.

첫 실행 시 빈 DB의 schema, 기본 전문가 9개, 용어 사전 기본값, `.env`의 `BOOTSTRAP_ADMIN_EMPLOYEE_ID`에 지정한 초기 관리자를 생성합니다. 기존 연구 데이터는 복원하지 않습니다. `raw/` 감시는 첫 실행부터 시작하고 이후 60초마다 PDF를 확인합니다. 원본 PDF는 포함하지 않습니다. 모델 설정 후 `raw/`에 PDF를 넣으면 수집과 Wiki 작성이 시작됩니다. 새 PDF와 생성된 DB·Wiki·실제 `.env`는 Git에서 제외됩니다.

## 연결 후 동작하는 범위

- PDF text 추출 → 문맥 chunking → keyword/vector 검색·reranking → 근거 기반 심층 답변.
- 앞선 대화를 참고하는 후속 질문, 실제 처리 단계 표시, 답변 중지와 대화 다시 열기.
- 공통·전문가별 Markdown Wiki 작성, 관련 변경 반영, 이력·목차·lint.
- 전문가 생성·수정·삭제, 논문 분류 필터, 자료 검토·수정 제안, 용어 사전, 사용자 권한.

**추가 구현이 필요한 부분:** 자동 OCR, LLM을 통한 canonical claim·수치·실험 조건 추출 및 한국어 초록 요약의 자동 저장, 실제 SSO 검증/프로필 API, 대화·북마크의 서버 저장과 팀 동기화. 현재 구조화 근거/초록 요약은 검증된 JSON을 가져오는 기능입니다. `.env`만 설정한다고 기존 수동 분석 결과까지 자동 재생성되지는 않습니다. 구체적인 연결 위치는 [이관 안내](docs/HANDOFF.md)를 참고하세요.

## 화면 수정 및 검증

화면을 수정할 때는 Node.js 22.12 이상과 `pnpm@11.25.0`을 사용합니다.

```powershell
pnpm install --frozen-lockfile
pnpm build
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.mjs
```

## 코드와 데이터 구성

앱 진입점은 `src/main.tsx` → `src/PilotApp.tsx`이며 실제 DB API `/api/pilot/*`를 사용합니다. `Pilot`이라는 파일명·API 경로는 기존 연결 호환성을 위해 유지하며 별도 실행 버전이 아닙니다.

`shared/expert_facets.json`과 `shared/terminology.json`은 실제 앱의 분류 정의와 용어 사전 초기값입니다. `shared/research_eval.example.json`은 검색 평가 도구의 입력 형식 예시입니다. 테스트에서 생성하는 합성 PDF·DB는 임시 폴더에서만 사용되며 실제 라이브러리에 자동 적재되지 않습니다.

실행·이관 기준은 이 README와 [HANDOFF.md](docs/HANDOFF.md)입니다. 현재 저장 구조는 [DB 설계](docs/DATABASE_PLAN.md), 전문가별 범위와 갱신은 [전문가 지식 설계](docs/EXPERT_KNOWLEDGE_PLAN.md)를 참고하세요.
