> **과거 시범 운영 기록입니다.** 2026-10-14 이관 시 기존 DB·Wiki·분석 JSON은 삭제했습니다. 아래의 이전 자료 복원 명령은 현재 이관본에 적용하지 않습니다. 설치·재구축은 [HANDOFF.md](HANDOFF.md)를 따르세요.

# 실제 PDF와 SQLite 시범

> **2026-10-13 최신 구현:** 실제 Markdown Wiki·공통 지식 재사용·범위 제한 검색·주기적 lint를 적용했습니다. 현재 실행 방식과 남은 사내 검증 항목은 [Markdown Wiki 운영 안내](MARKDOWN_WIKI.md)를 우선합니다. 아래의 Wiki 엔진/답변 검색이 미구현이라는 설명은 이전 단계의 기록입니다.


**사용자 화면 정리(2026-10-13):** 앱 진입점을 실제 DB 화면으로 통일했다. <http://127.0.0.1:8011/>에서 시작하며 `#ask`, `#library`, `#experts`, `#team`, `#personal`, `#ingest`, `#settings`, `#guide`를 사용한다. 기존 `#pilot/*` 링크도 호환된다. '실제 논문 시범'·Pilot·목업 비교·DB 확인 메뉴와 구현 비교표는 사용자 화면에서 제거했다. DB schema API와 개발 문서는 유지한다. 기존 목업은 참고 소스로 보관하며 앱에서 전환하지 않는다. 실제 연결 전인 업로드는 비활성화했고 관리자 전문가 추가는 실제 DB에 연결했다.

2026-10-13 기준. 사용자 PDF 41편 전부를 Codex가 직접 읽어 주요 claim·수치·조건·Wiki를 작성했다. 이번 추가분은 38편이며 외부 LLM API는 호출하지 않았다. 원본 PDF, 구조화 JSON과 SQLite DB는 Git에서 제외한다.

## 화면에서 확인

전문가 카드 우측 상단의 '삭제'는 대상 이름과 영향 범위를 확인하는 팝업을 연다. 확인 후 목록·추천·전문가 검색에서 제외되고 Wiki 연결이 해제된다. 원천 논문·claim·수치와 공통 Wiki 본문은 보존한다. 삭제 알림의 '실행 취소'로 설정과 지식 연결을 복원할 수 있으며 알림을 닫거나 새로고침하면 이 버튼은 사라진다. 삭제·복원 API도 관리자만 사용할 수 있다. 전문가 특화 종합 Wiki는 별도 후속 설계이며 현재 공통 Wiki와 혼동하지 않는다.

서버 실행 후 <http://127.0.0.1:8011/#library>을 연다. 왼쪽 주메뉴, 최근 질문, 내 페이지, 자료 현황과 관리자 전문가 설정을 이용한다.

- **연구 질문**: 영어 전문 용어로 질문 → 담당 논문을 사용한 전문가 추천 → 자동 추천 또는 직접 복수 선택 → 해당 담당 논문 안에서 claim과 연결된 수치·조건·원문 조회. 예: `FeFET endurance`, `OCTRAM retention`, `CFET SRAM`, `NAND erase`. 추천과 검색은 키워드 규칙이며 LLM 생성 답변은 아니다.
- **논문 라이브러리**: 실제 PDF 41편을 8편씩 조회한다. 오른쪽 카테고리에 메모리 종류·연구 초점·연구 계층·응용 방식·연구 방식을 표시한다. 같은 축은 OR, 다른 축은 AND로 검색·전문가 필터와 함께 적용한다. 숫자는 다른 축 필터를 적용한 후보 수이며 다중 분류 논문은 여러 카테고리에 집계될 수 있다. 미분류도 탐색 가능하다. 선택 칩·개별 해제·초기화가 있고 모바일에서는 펼치는 패널을 사용한다. 논문 상세에서 claim, 연결 근거 인용, 원본 PDF와 북마크를 확인한다. 닫기 버튼은 상단에 고정된다.
- **전문가 지식**: 전문가 9명의 담당 논문과 Wiki를 조회한다. Wiki는 6개 주제씩, 펼친 근거는 3개 claim씩 표시한다. 마지막 반영 시각과 연결된 원문을 확인할 수 있다.
- **팀의 질문 / 내 페이지**: 이 세션에서 조회한 실제 근거 snapshot을 다시 연다. 공유·비공개·삭제·복구는 화면 시연이며 다른 사용자에게 전달되지 않는다. 질문 기록은 새로고침하면 초기화된다. 북마크만 해당 브라우저에 저장한다.
- **자료 현황**: 하단 health 카드에서 확인한다. 문제가 있는 5페이지와 실제 지식 적재 작업 2회의 추가 이력이 표시된다.
- **전문가 설정**: 우측 상단 '전문가 추가'에서 이름·개요·분류를 입력하고 편입 미리보기를 확인한다. 모든 분류는 전체 선택이 기본이며 축 하나라도 모두 해제하면 자동 편입하지 않는다. 애매한 논문은 직접 편입·제외할 수 있다. 생성하면 정책과 담당 논문이 DB에 저장되고 기존 claim·수치·Wiki가 연결되며 새로고침해도 유지된다. 기존 전문가 카드에는 '설정 확인·수정'과 '전문가 지식' 버튼이 있다. 설정 팝업은 이름·개요·분류·직접 편입/제외·활성 여부를 조회·수정하며, 미리보기 후 변경사항을 실제 DB에 저장한다. 분류 기록이 없는 기존 전문가는 현재 담당 논문을 직접 편입으로 보존한 설정을 표시한다. 팝업을 닫거나 취소하면 저장하지 않는다. 설정을 열어 둔 사이 다른 관리자가 수정하면 409로 알리고 최신 설정을 다시 열도록 안내한다.
- **사용 가이드**: 질문·전문가 지식·논문·개인 페이지 이용 방법을 설명한다.

`#ask`, `#library`, `#settings`는 모두 실제 자료를 사용하며 API 내부 경로 `/api/pilot/*`는 유지한다. 기존 `/api/papers`는 참고용 fixture API로 현재 앱에서 사용하지 않는다. [목업 비교 점검](PILOT_PARITY_REVIEW.md)에 남은 차이를 기록했다.

전문가 생성과 라이브러리는 `shared/expert_facets.json` 및 동일한 분류 캐시를 재사용한다. 새 전문가 때문에 PDF를 다시 파싱하거나 claim·Wiki 본문을 복제하지 않는다. 서버 시작·새 PDF 적재·근거 적재 시 신규 전문가의 저장 정책과 수동 결정을 다시 적용한다. 개요는 소개 문구이며 자동 분류 추천 기능은 아직 연결하지 않았다. 서버 환경변수 `EVIDENCE_USER_ROLE=admin|member`(기본 admin, 미지정 시 `VITE_DEMO_ROLE` fallback)로 로컬 역할을 구분한다. member는 전문가 생성·설정 조회/수정·미리보기·분류 정의 API에서 403을 받으며 설정 메뉴도 숨겨진다. 이는 사용자별 인증이 아니므로 사내 SSO 세션 역할로 교체해야 한다.

## 한국어 초록 요약 · 2026-10-13

라이브러리 카드와 논문 상세의 영어 초록 문단을 ‘초록 요약 · AI 요약’으로 교체했다. Codex가 현재 41편의 저장된 초록을 읽고 한국어 2~3문장으로 정리했으며 전문 용어는 영어로 유지했다. 실험·simulation·제안·review를 구분하고 본문·caption이 섞인 추출 결과에서는 초록 부분만 사용했다. 외부 LLM API는 호출하지 않았다.

`paper_abstract_summaries`에 요약·제목/초록 fingerprint·작성 주체·prompt version·시각을 저장한다. 원본 `papers.abstract`, claim·수치·Wiki는 그대로 보존한다. 제목이나 초록이 달라지면 기존 요약을 표시하지 않으며 요약이 없는 논문은 원본 PDF 확인을 안내한다. 이 탐색용 요약을 별도의 답변 근거로 색인하지 않는다.

전달 시 `data/curated_abstract_summaries_ko.json`도 함께 가져간다. 원천 논문 적재와 제목 교정을 마친 뒤 `python -m backend.abstract_summaries data/curated_abstract_summaries_ko.json`으로 적용한다. CLI가 기존 DB를 먼저 백업하며 원문 fingerprint 불일치 시 전체 요약 적재를 취소한다. 동일한 payload 재적용은 무변경이다.

새 PDF의 자동 요약은 아직 실행하지 않는다. 사내 LLM 연결 시 `backend/prompts/abstract_summary_ko.txt`와 같은 입출력 계약을 사용하고, 읽은 원문의 fingerprint·실제 모델명을 함께 저장한다. 사용자 화면에는 폴더·생성 파이프라인을 노출하지 않는다.

## 저장 결과

| 데이터 | 전체 | 이번 추가 |
|---|---:|---:|
| 논문 / PDF | 41 / 41 | 기존 적재 결과 사용 |
| 페이지 / 검색 구간 | 164 / 733 | 원문 유지, token 기반 재색인 |
| 상위 검색 문맥 / 복수 페이지 구간 | 306 / 101 | 섹션·parent–child 구조 |
| claim | 133 | 124 |
| 주요 수치 | 141 | 130 |
| 수치별 조건 항목 | 334 | 314 |
| Wiki 본문 | 41 | 38 |
| 전문가 | 9 | 6 |
| 전문가–Wiki 연결 | 64 | 공통 본문 재사용 |
| 원문 근거 구절 | 218 | 중복 구절은 재사용 |

조건 334개 중 225개는 원문 값과 근거가 있고 109개는 미기재(NULL)다. 원문에 없는 조건은 추측하지 않았다. 정성적 review·perspective에는 수치를 억지로 추가하지 않았다. 측정·시뮬레이션·추정, 선행 연구 인용과 적용 한계를 구분한다. 모든 claim은 `human_review_pending`이며 문자열 일치 검사가 과학적 사람 검토 완료를 의미하지 않는다.

[논문별 적재 결과](CORPUS_REVIEW_2026-10-13.md)에 41편별 claim·수치·조건·전문가 수록 범위를 정리했다. 주요 내용을 선별했으며 모든 문장·도표를 완전히 구조화한 것은 아니다. 전문가별 조회는 로컬 등록 자료 안에서만 동작한다. 기존 전문가 소속은 초기 제목·초록 규칙과 원문 검토 결과를 유지한다. 신규 전문가와 카테고리 필터는 제목·초록 기반 5축 규칙 분류를 공유하며 결과를 fingerprint와 함께 캐시한다. 메모리 종류는 제목에 나타난 신호만 사용하므로 미분류 자료는 검토가 필요하다. Report AI 전체 taxonomy 엔진·분류 LLM을 이식하거나 정확도를 검증한 것은 아니다.

제목 자동 추출이 실패했던 4편을 포함해 제목 6건을 원문 대조 후 교정했다. 이전 값, 새 값, 출처와 작업 ID는 `paper_metadata_changes`에 보존한다.

## 실제 관계

```text
papers → paper_documents → extraction_runs → pages
extraction_runs → search_contexts → search_chunk_details → search_chunks → FTS5
search_chunks → search_chunk_spans → pages
papers → claims → claim_evidence → evidence_spans → pages
claims ↔ claim_measurements ↔ measurements → condition_values
measurements → measurement_evidence → evidence_spans
expert_categories → expert_papers → papers
expert_categories ↔ expert_knowledge_pages ↔ knowledge_pages → knowledge_links → claims
curation_runs → claims / measurements / knowledge_pages / paper_metadata_changes
```

[실행 DDL](../backend/pilot_schema.sql)은 운영용 PostgreSQL 설계의 일부를 SQLite로 구현한 것이다. 복합 외래키로 다른 논문의 claim·수치·원문을 잘못 연결하지 못하게 한다. Wiki는 같은 claim·수치·조건을 참조하며 PDF를 다시 분석하지 않는다. Wiki의 모든 근거 논문을 담당하는 전문가만 해당 Wiki를 공유한다. 서버 시작과 신규 구조화 적재 시 공유 연결을 동기화한다.

원문 구절은 공백 정규화 후 유일한 일치 위치를 찾아 실제 페이지 문자열의 offset과 원문을 저장한다. 숫자는 바꾸지 않는다. 구절을 찾지 못하거나 PDF 해시가 다르거나 담당 범위가 맞지 않으면 구조화 적재 전체를 취소한다. 같은 PDF와 같은 JSON의 재적용은 idempotent하다.

기존 ID의 구조화 내용을 덮어쓰지 않는다. 별도 버전 ID로 추가할 수 있지만 논리 ID·revision·승인·철회·본문 diff·답변 snapshot을 갖춘 운영용 버전 시스템은 아직 구현하지 않았다. 이번 화면의 '지식 반영 이력'은 실제 추가 작업 이력이다.

## 검색 분할과 수치 표시 · 2026-10-13

논문 상세의 '페이지 원문'과 p.1, p.2 등의 페이지별 버튼은 제거했다. 원본 PDF 열기와 claim에 연결된 원문 인용·페이지 정보는 유지한다. 검색 적중 구간에서는 '앞뒤 문맥 함께 보기'로 해당 상위 문맥만 펼친다.

실제 DB에는 섹션·문장 경계와 token 한도를 고려한 구조 기반 분할을 적용했다. 추가로 사내 embedding으로 인접 문장 묶음의 의미 변화를 판단하는 분할을 구현했다. `.env`에는 주소·키·모델 및 모델 입력 한도·tokenizer 경로만 남겼고 검색 구간 상한·겹침·의미 임계값은 코드 기본값을 사용한다. 실제 사내 모델로 재색인하기 전이므로 현재 733개 검색 구간은 유지한다. [사내 연결 절차](HANDOFF.md#사내-embedding-연결-절차)와 [DB 설계](DATABASE_PLAN.md#현재-로컬-검색-분할과-고도화-기준)를 따른다. pypdf 추출의 표·다단 레이아웃 한계는 남아 있다.

기존 DB를 바꿀 때는 `.\.venv\Scripts\python.exe -m backend.reindex_search`를 실행한다. 정책이 같으면 건너뛰고 변경 시 자동 온라인 백업 후 검색 파생 테이블만 트랜잭션으로 교체한다. 이번 작업 전 최초 백업은 `data/backups/evidence-studio-before-chunking-20261013T043537034049Z.sqlite3`이다. 41편의 원천·claim·수치·조건·Wiki 등 22개 테이블 hash가 변경되지 않았고 모든 출처 span·token 한도·FK와 41편 재실행 무변경을 확인했다.

수치는 화면과 복사 결과에 공통 scientific notation을 적용한다. 예: `1.20e-3 → 1.20 × 10⁻³`, `65k → 6.5 × 10⁴`. DB 원문과 직접 인용은 보존하고 명확한 단일 지수 표현과 계산용 값이 다르면 구조화 적재를 중단한다. 현재 141개 수치에서 이 불일치는 발견되지 않았다. 규칙과 모호한 OCR 문자열의 처리 한계는 [수치 표기 기준](TERMINOLOGY.md#수치와-scientific-notation)을 참고한다.

## 실행과 재적재

사내 모델 설정은 프로젝트 루트 `.env`에서 관리합니다. 키·모델명·서비스 주소 및 설정 우선순위는 [README의 사내 모델 설정](../README.md#사내-모델-설정)을 참고하세요. reranker 키는 embedding 키를 재사용합니다. 서버 시작만으로 API를 호출하지 않으며, embedding을 설정한 뒤 PDF 적재·재색인에서 실제 호출합니다. 확인된 Bearer 인증·문자열 input/model 요청·data[0].embedding 응답은 코드 기본값입니다. `python -m backend.reindex_search --check`는 테스트 문장만 보내고 DB를 수정하지 않습니다.

프로젝트 루트에서 Python 3.12 이상, FTS5가 있는 SQLite, Node.js 22.12 이상과 pnpm을 사용한다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock.txt
.\.venv\Scripts\python.exe -m backend.pilot_ingest
.\.venv\Scripts\python.exe -m backend.pilot_curate data/curated_evidence.json
.\.venv\Scripts\python.exe -m backend.pilot_curate data/curated_remaining_38.json
pnpm install
pnpm build
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8011
```

이미 적재와 빌드가 끝난 현재 환경에서는 마지막 서버 명령만 필요하다. FastAPI가 `dist/`와 API를 함께 제공한다. 정적 서버만으로 실제 DB 화면은 동작하지 않는다. 서버는 로컬 루프백에 바인딩한다.

새 PDF를 `raw/`에 넣고 ingest를 실행하면 본문과 키워드 색인까지 추가한다. `--watch 60`을 사용하면 프로세스 실행 중 주기적으로 검사한다. 현재 상시 watcher는 켜지 않았다. watcher는 claim과 Wiki를 작성하지 않는다. 새 구조화 JSON을 작성하거나 사내 LLM을 연결해야 지식이 추가된다.

`--raw`, `--db`로 적재 경로를 지정하고 서버는 `EVIDENCE_DB_PATH`를 지원한다. 같은 해시의 다른 경로는 출처로 추가하며 같은 경로의 내용 변경은 기존 근거를 보존하고 검토 대상으로 둔다. 최소 5초 파일 안정화와 읽기 전후 크기·mtime·해시를 확인한다. Report AI 앱·워커·DB는 실행하거나 변경하지 않는다.

다른 PC에는 소스·빌드, 원본 PDF와 **두 JSON 모두**(`curated_evidence.json`, `curated_remaining_38.json`)를 함께 가져간다. 새 raw 위치에서 ingest 후 위 순서로 두 curation을 적용한다. 기존 DB를 복사했어도 ingest로 새 PDF 경로를 등록한다. 원본 API는 등록된 경로 중 해시가 일치하는 파일만 제공한다. 이번 추가 전 DB 백업은 `data/backups/before-remaining-38-2026-10-13.sqlite3`에 있다.

## 검증과 남은 범위

- Python 통합 테스트 10개, 기존 목업 Node 테스트 28개 및 TypeScript·Vite 빌드 통과.
- 전체 41편에 claim 존재, 모든 claim의 Wiki 연결, Wiki 전문가 범위 위반 0건.
- 원문 구절 218개·검색 구간 712개의 페이지 offset 일치, SQLite integrity_check=ok, FK 위반 0건.
- 같은 추가 JSON 재적용은 unchanged. `data/verification-2026-10-13.json`에 상세 결과 저장.
- 159페이지는 키워드 검색 가능하다. OCR 필요 4페이지와 문자 품질 확인 1페이지는 여전히 색인에서 제외한다. 관련 페이지는 원본 PDF에서 확인할 수 있다. OCR 모델은 실행하지 않았다.
- 답변 검색은 FTS5와 일부 한국어 별칭을 지원한다. embedding 기반 문맥 분할 호출부는 준비됐으나 실제 사내 모델 실행·품질 평가는 남아 있다. 답변용 vector 검색·BGE reranker·LLM 라우팅·생성 답변, 질문의 정확한 조건 필터와 수치 비교 엔진은 미연결이다.
- Wiki 추가는 실제 DB에 저장했으나 자동 갱신·질문 빈도 기반 FAQ·본문 revision 비교는 기존 목업에서만 시연한다.
- 팀 공유·SSO·서버 권한·업로드 및 동시 사용자 동기화는 후속 사내 구현 범위다. 기존 목업과 같은 역할 플래그는 UI 검토용이며 서버 보안 경계가 아니다.
