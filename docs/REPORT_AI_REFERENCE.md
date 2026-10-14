# Report AI 참조 기록

## 2026-10-13 용어 사전

후속 사용자 요청으로 Evidence Studio의 사전은 **대표 용어 + 동의어**로 단순화했습니다. 아래는 초기 참고 원칙입니다. 검색 연관어·적용 분야·사용 주의점은 현재 화면과 검색·LLM 적용에서 제외했으며 과거 DB 값과 이력만 호환 목적으로 보존합니다.

같은 활성 경로의 `src/search_dictionary.py`, `tests/test_search_dictionary.py`, README 및 web 화면을 읽기 전용으로 참고했습니다. Report AI의 운영 사전 DB를 가져오거나 앱을 실행하지 않았습니다.

Evidence Studio에 적용한 원칙:

- 같은 의미의 동의어와 검색 전용 연관어를 구분하고 승인한 항목만 실제 적용
- 원문 인용·제목·수치·단위 보호, Unicode 정규화는 비교에만 사용
- 순수 단위는 용어 사전에서 제외하고 parameter·재료명은 허용
- revision을 통한 동시 수정 충돌 방지, 불변 이력과 보관 상태, 재시작 시 삭제/보관 항목을 되살리지 않는 초기 가져오기
- 전체 사전 대신 입력에 관련된 제한된 용어만 LLM에 전달

이 프로젝트에는 일반 유저 제안 → 관리자/슈퍼 유저 승인 권한 흐름을 적용했습니다. Report AI의 자동 후보 수집·검색 실패 수집·복잡한 중복 병합 기능은 이식하지 않았습니다. 승인 항목 간 중복은 명시적 오류로 처리합니다. 초기 항목은 기존 Evidence Studio 사전과 검색 별칭에서 가져옵니다. 구현·검증 범위는 [용어 지침](TERMINOLOGY.md)에 기록합니다.

2026-10-12 사용자 요청에 따라 활성 경로 `C:\codex project\report AI`의 코드·README·테스트를 읽기 전용 검토했습니다. 운영 PDF·DB·자격 증명 및 이전 OneDrive 복사본은 열거나 복사하지 않았고 앱·워커·테스트를 실행하지 않았습니다. 아래 내용은 정적 검토 결과이며 현재 운영 성공을 재검증한 것은 아닙니다.

| 참고한 코드/테스트 | 확인한 동작 및 이관 기준 |
|---|---|
| `src/corpus_pipeline.py`, `extract_page_text` | pypdf 먼저 사용, 인코딩 문제/텍스트 부족 시 RapidOCR 전환. 추출 출처를 페이지별 보존 |
| `ocr_reading_order`, `ocr_page` | pypdfium2 렌더, 2단 읽기 순서. 휴리스틱을 범용 파서로 단정하지 않고 표·수식·혼합 레이아웃 검증 |
| `is_visually_blank_pdf_page`, 캐시 로직 | 빈 페이지와 실패 구분, 손상된 부분 캐시 재생성. 새 캐시는 해시·페이지·파서 버전 키 검토 |
| `tests/test_corpus_pipeline.py` | 제목에 저자/소속/세션 코드 혼입, 글리프 손상, 잘린 제목, 초록 오염 회귀. 추출과 메타데이터 검증 상태 분리 |
| `src/paper_scanner.py` | 기본 60초 안정화, 크기·mtime 확인, 독립 파일 발견 루프와 단일 무거운 수집 소비자 |
| `_process_job`, `register_duplicate_file` | SHA-256 정본과 복수 경로 보존. 같은 파일명 내용 변경은 HUMAN_REVIEW_REQUIRED |
| `tests/test_paper_scanner.py` | 실패 PDF 개별 격리, 선택 재시도, 지연/backoff, 파일 유실 시 DB 자동 삭제하지 않음 |
| README Evidence-first schema v3, `src/research_evidence.py` | 페이지·절·claim·수치·단위·조건·Figure 계보. raw 표현과 normalized 값 구분. 수동 검토 보존 |
| `src/paper_embeddings.py` | 정본 corpus.db와 파생 paper_embeddings.db 분리. stable chunk ID, content hash, 모델별 색인, parent section 연결 |

목업에 적용한 사용자 경험은 확인 필요 자료의 분리, 개별 재시도, 답변과 논문·원문 위치의 연결입니다. 폴더 경로와 처리 단계는 사용자 요청에 따라 일반 제품 화면에서 제거했습니다.

추가한 [실제 로컬 시범](LOCAL_PILOT.md)에서는 `src/corpus_pipeline.py`의 제목·초록 분리 및 문자 품질 판정에 필요한 순수 함수와 상수만 `backend/report_metadata.py`로 복사했습니다. Report AI를 import하거나 운영 앱·워커·DB를 실행하지 않습니다. SHA-256 중복 판정, 파일별 실패 분리, 페이지 원문 계보는 별도 시범 코드로 구현했습니다. RapidOCR 및 전체 taxonomy 엔진을 이식한 것은 아닙니다. 최초 제목 추출에 실패한 4편을 포함해 총 6건의 제목을 2026-10-13 원문 검토에서 직접 교정하고 이전 값과 근거를 보존했습니다. 페이지 5개는 계속 확인이 필요하며 최적화된 파서를 완전히 재현한 것은 아닙니다.

운영 워커·작업 큐·전체 OCR·검색 모델 연동은 사내 개발에서 이어갑니다. Report AI의 현재 정본은 SQLite이며 PostgreSQL 이관 문서를 이관 완료로 해석하지 않습니다.

## 논문 분류에서 재사용할 부분

2026-10-13 라이브러리 후속 변경: `web/index.html`의 `paperFacetPanel`, `web/styles.css`의 오른쪽 고정 패널·접기·모바일 overlay, `src/paper_manager.py`의 같은 축 OR / 축 사이 AND 필터를 읽고 탐색 구조를 참고했다. Evidence Studio의 `LibraryCategories.tsx`는 5축 그룹, 논문 수, 미분류, 선택 해제·초기화를 제공한다. 숫자는 해당 축 이외의 선택 조건을 반영한다. `expert_config.py`가 제목·초록을 사용한 간단한 분류를 fingerprint와 함께 캐시하고 전문가 생성과 라이브러리가 공유한다. Report AI 분류 엔진을 그대로 가져오거나 운영 DB를 변경하지 않았다.

같은 활성 경로의 분류 코드·설정·테스트를 추가로 읽었습니다. 다음은 확인한 구현이며 LLM 분류 호출이나 성능 벤치마크를 실행한 결과가 아닙니다.

| 참고한 코드/테스트 | 확인한 동작 | Evidence Studio 적용 방향 |
|---|---|---|
| `config/memory_taxonomy.json`, `src/corpus_pipeline.py::classify_paper` | 제목·초록의 사전/정규식 신호로 주제 판정. 제목 일치 5, 초록 일치 2의 가중치. `MEMORY_CORE`, `MEMORY_RELATED`, `GENERAL_SEMICONDUCTOR` 구분 | 최초 분석에서 공통 분류를 저장하고 전문가 생성마다 다시 읽지 않음. 점수를 확률로 해석하지 않음 |
| `classify_paper`, `tests/test_corpus_pipeline.py` | ESD의 Human Body Model을 메모리 HBM으로 잡지 않는 예외. 제목 미추출·OCR 필요·일부 학회 부속 자료를 검토 대상으로 구분하는 회귀 사례 | 약어 문맥과 추출 품질을 확인. 추출 실패 상태를 정상적인 분야 분류와 분리 |
| `src/paper_taxonomy.py::FACET_DEFINITIONS`, `infer_paper_taxonomy` | platform, application, layer, focus, method의 다중 축. layer/focus/method는 제목 신호 우선, 제한된 초록 fallback, 각 축 최대 두 후보와 primary 표시 | platform·focus부터 활용하고 나머지는 선택적으로 보존. 분야·응용·연구 초점을 하나의 배타적 category로 섞지 않음 |
| `src/paper_taxonomy.py::_rank_pattern_tags` | 기존 분류와 수동 override 출처를 유지하고, 모호한 옛 분류는 무리하게 새 태그로 치환하지 않음 | 판정 방식·이유·근거와 관리자 교정을 보존. Report AI의 고정 confidence 숫자를 검증된 정확도로 사용하지 않음 |
| `src/paper_manager.py::_taxonomy_source_fingerprint`, `_refresh_taxonomy_tags` | 제목·초록·주제·기존 분야·출처·taxonomy 버전으로 fingerprint 구성. 동일 fingerprint와 버전은 갱신 생략 | 저장된 메타데이터로 변경분만 재분류. 규칙 변경만으로 OCR·문서 임베딩 재생성하지 않음 |
| `src/paper_manager.py::list_papers`의 taxonomy filter | 같은 축의 여러 값은 OR, 선택한 서로 다른 축은 AND로 필터 | 전문가 담당 범위를 설명 가능한 분류 조합으로 정의하되 미분류 후보 보완 탐색을 병행 |
| `tests/test_paper_taxonomy.py` | RRAM+CIM/PIM과 회로·신뢰성·성능·실험의 분리, 수동 legacy 분류, 복수 신호의 초록 fallback, characterization을 Reliability로 강제하지 않는 사례 | 사내 이관 시 회귀 검증 기준으로 사용; 이번 작업에서 테스트를 실행한 것은 아님 |

공통 분류만으로 전문가 지식 편입이 완성되지는 않습니다. Evidence Studio에서는 기존 분류로 후보를 모은 뒤 담당 범위와 포함·제외 조건을 확인합니다. 규칙으로 명확한 항목은 자동 편입하고, 부족한 경우에만 기존 논문 카드와 제한된 본문을 사내 LLM으로 검토하며, 남은 모호한 항목만 관리자에게 보냅니다. 이 전문가 편입과 Wiki 갱신은 Report AI에서 확인한 기능이 아니라 [전문가 지식 설계](EXPERT_KNOWLEDGE_PLAN.md)의 추가 제안입니다.

후속 구현 시 복사 중 파일, 이름만 다른 중복, 같은 경로 내용 변경, 공백·손상 페이지, 부분 캐시, 2단 문서, 제목 경계, 과학적 표기, 실패 후 다른 파일의 처리, 재시작 중복 방지, 수동 검토 이력 보존, 인용의 원문 위치를 확인합니다. 이 기술 정보는 사용자 화면이 아닌 개발 참고 자료에만 둡니다.
