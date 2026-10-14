# Evidence Studio DB 구성

이 문서는 현재 실행 코드의 저장 구조와 확장 계획을 구분합니다. 실제 스키마는 [pilot_schema.sql](../backend/pilot_schema.sql), 초기화·호환 처리는 [pilot_db.py](../backend/pilot_db.py)에 있습니다. 기본 저장소는 SQLite이며 PostgreSQL·pgvector로 이관된 상태는 아닙니다.

## 데이터의 책임

| 영역 | 현재 저장 구조 | 역할 |
|---|---|---|
| 논문·원문 | papers, paper_documents, document_sources, pages, extraction_runs | 파일 SHA-256, 원문과 추출 상태, 원본 PDF 위치 |
| 원천 근거 | claims, evidence_spans, claim_evidence | 주장과 정확한 원문 구간의 연결 |
| 수치·조건 | measurements, claim_measurements, measurement_evidence, condition_values | parameter, raw 값·단위, 실험·모델 조건과 주장 사이 연결 |
| 분류·전문가 | paper_classifications, paper_topics, expert_categories, expert_policies, expert_papers | 공유 분류 캐시와 전문가별 담당 범위 |
| Markdown Wiki | wiki_files, wiki_revisions, wiki_jobs, wiki_topic_requests | 실제 .md 파일의 경로·hash·의존 근거·불변 버전 및 갱신 작업 |
| 검색 | search_chunks, search_contexts, search_chunk_spans, retrieval_units, FTS 색인 | 문맥 구간·정확한 페이지 offset, 키워드 및 vector 검색 |
| 작업·검토 | processing_tasks, ingestion_jobs, raw_watcher_status, page_review_requests, page_review_changes | 수집·Wiki 작업 상태와 사용자 교정 이력 |
| 사용자·용어 | users, user_audit, terminology_entries, terminology_revisions | 권한, 접속 사용자, 승인된 대표 용어·동의어 및 수정 이력 |
| 답변 실행 | answer_runs | 소유자, 질문, 근거 snapshot, 모델·지침 hash, 검색과 검증 결과 |

## 논문과 전문가의 분리

논문·claim·수치·조건은 공용 원천 데이터입니다. 전문가를 만들 때 이를 복제하거나 다시 추출하지 않습니다. 동일 분류 축 안에서는 OR, 서로 다른 축 사이는 AND로 담당 논문을 고르고 수동 포함·제외를 적용합니다. 전문가는 담당 논문의 claim까지 질의에서 사용하며, 숫자만 따로 검색 결과에 붙이지 않고 연결된 parameter·조건·원문을 함께 제공합니다.

전문가 삭제는 공용 논문·claim·수치·공통 Wiki를 지우지 않습니다. 전문가 특화 Wiki는 해당 전문가의 활성 상태와 의존 근거 유효성에 따라 검색에서 제외됩니다. 삭제 실행 취소와 과거 버전 보존 규칙은 [전문가 지식](EXPERT_KNOWLEDGE_PLAN.md)과 [Markdown Wiki](MARKDOWN_WIKI.md)를 참고합니다.

## Wiki와 검색

Wiki 본문의 원본은 `wiki/` 아래 Markdown입니다. DB는 파일 메타데이터·버전·의존 관계·검색 캐시를 관리합니다. `knowledge_pages` 등 옛 구조화 기록은 호환·이관을 위해 유지하지만 화면 Wiki 본문은 실제 파일에서 읽습니다. 원천 claim과 수치를 재분석해 중복 저장하는 대신, 공통 원천 Wiki와 여러 논문의 공통 주제 종합을 재사용합니다.

검색 구간은 페이지 경계 자체가 아니라 문단·절과 embedding 유사도 경계로 구성합니다. 길이 제한은 API 입력과 문맥 예산을 지키는 보조 장치입니다. 사내 embedding이 없으면 구조 기반 fallback임을 구분합니다. 키워드 FTS와 vector 유사도 결과를 합치고, 설정된 reranker로 다시 정렬합니다. 현재 vector 검색은 SQLite에 보관한 벡터의 정확 유사도 비교이며 ANN 엔진은 아닙니다. 자세한 계약은 [검색 구현](HANDOFF.md)을 확인합니다.

## 대화와 권한

대화 목록은 브라우저에서 사번별로 저장하며 한 대화에 여러 질문·답변을 묶습니다. 후속 질문은 최근 완료 답변의 `run_id` 최대 6개를 서버에 보냅니다. 서버는 본인 소유의 완료 기록만 읽고 각 이전 질문 최대 1,000자·답변 최대 2,200자를 문맥으로 사용합니다. 이전 답변을 새 논문 근거로 취급하지 않고 현재 전문가 범위에서 다시 검색·검증합니다.

`answer_runs`는 답변 당시 근거를 보존하는 불변 snapshot입니다. 관리자도 타인의 실행 기록을 읽을 수 없습니다. 대화 삭제는 브라우저 목록의 휴지통 처리이며 서버 snapshot 삭제와 다릅니다. 팀 공유 표시는 현재 브라우저 내 상태로, 사용자 간 서버 동기화 기능을 의미하지 않습니다.

## 사내에서 이어갈 범위

- 실제 SSO 쿠키 검증·사번별 프로필 API 어댑터 연결.
- 논리적 논문과 여러 PDF 버전의 통합, claim·수치 자체의 revision·철회 정책.
- 표·그림·수식 OCR 및 자동 LLM claim/수치/조건 추출·승인 파이프라인. 현재 검증된 JSON 가져오기 경로를 사용합니다.
- 데이터량을 측정한 뒤 PostgreSQL/ANN 이관, 자료별 ACL, 대화 서버 동기화와 보존·삭제 정책.
- 검색·생성 품질은 실제 사내 모델과 팀 질문으로 [비교 평가](RESEARCH_EVALUATION.md).

공개 배포본에는 운영 DB·Wiki·원본 논문이 없습니다. 앱 시작 시 빈 DB와 기본 설정을 초기화하며 사내 자료로 구축합니다.
