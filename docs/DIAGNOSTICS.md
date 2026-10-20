# 사내 단계별 점검

`python -m backend.diagnostics`로 실행할 단계를 선택하고 독립 실행형 HTML 보고서를 확인합니다. 앱 서버를 시작하거나 운영 `raw/`를 스캔하지 않습니다. 기본 실행은 외부 모델을 호출하지 않으며, 실제 API에는 `--live`를 명시합니다.

## 시작하기

프로젝트 루트에서 앱과 같은 Python 환경을 사용합니다. 기존 `.env`를 덮어쓰지 마세요. 설치는 사내 승인 패키지 저장소 또는 준비한 wheel을 사용합니다.

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt

# 지원 단계와 필요한 서비스를 확인
.\.venv\Scripts\python.exe -m backend.diagnostics --list

# 기본 점검: 기존 백엔드 테스트 + PDF/DB + claim/수치 + 구조 기반 분할
.\.venv\Scripts\python.exe -m backend.diagnostics

# 필요한 단계만 선택
.\.venv\Scripts\python.exe -m backend.diagnostics --steps ingest,curation
~~~

선택한 순서대로 한 단계씩 실행합니다. 각 단계는 별도 임시 DB에서 자체 입력을 준비하므로 이전 단계를 먼저 실행할 필요가 없습니다. 이전 단계의 실패가 나머지 독립 점검을 막지 않습니다. 종료 시 출력되는 `report.html`을 브라우저로 열면 됩니다. 외부 CSS·JavaScript·폰트·이미지가 필요하지 않습니다.

## 실제 사내 API 점검

프로젝트 `.env`에 작성한 주소·키·모델을 사용합니다. 기본/경량 LLM은 공통 LLM 연결을, Reranker는 Embedding과 동일한 URL·키를 사용합니다. [설정 안내](CONFIGURATION.md)를 참고하세요. 프로세스 환경변수가 `.env`보다 우선하며, `--env-file`로 별도 설정 파일을 지정할 수도 있습니다. 상대 tokenizer/contract 경로는 앱과 동일하게 프로젝트 루트 기준입니다.

~~~powershell
# 먼저 작고 저렴한 개별 연결 점검
.\.venv\Scripts\python.exe -m backend.diagnostics --steps llm,embedding,reranker --live

# 선택 사항: 경량 모델
.\.venv\Scripts\python.exe -m backend.diagnostics --steps llm-light --live

# 실제 embedding을 사용하는 문맥 분할
.\.venv\Scripts\python.exe -m backend.diagnostics --steps chunking --live

# 앞선 개별 점검이 통과한 뒤 Wiki와 답변 전체 흐름
.\.venv\Scripts\python.exe -m backend.diagnostics --steps wiki,answer --live

# 다른 설정 파일로 비교
.\.venv\Scripts\python.exe -m backend.diagnostics --steps llm --live --env-file .env.company

# 모든 단계 (기본·경량·embedding·reranker 및 전체 흐름을 다시 점검)
.\.venv\Scripts\python.exe -m backend.diagnostics --steps all --live
~~~

| 단계 | 점검 범위 | 실제 API |
|---|---|---|
| unit | 기존 Python 단위·회귀 테스트 전체. 가상 응답·로컬 HTTP 테스트 서버 사용 | 외부 호출 없음 |
| ingest | 합성 PDF 추출, 검색 chunk 저장, 중복 PDF 재사용 | 없음; 항상 구조 기반 분할 |
| curation | 준비된 JSON의 claim·parameter·수치·조건·정확한 인용 위치와 전문가 검색 범위 | 없음 |
| chunking | 원문 위치·입력 길이 제한; `--live`일 때 embedding 기반 경계 분석 | live일 때 embedding |
| llm | 기본 모델 JSON 응답, 단순 수치·조건·추정 구분 | 기본 LLM 1회 |
| llm-light | 경량 모델의 동일 계약 | 경량 LLM 1회 |
| embedding | 두 짧은 문장의 벡터 유효성·차원 일치·서로 다른 벡터 | 기본 계약 기준 2회 |
| reranker | 문서 인덱스·점수 계약, 명백히 관련 있는 문서의 우선 순위 | Reranker 1회 |
| wiki | 원문→원천·공통 Wiki, 변경 없는 결과 재사용, 전문가 특화 노트, 새 논문 반영, 구조 lint | 기본/경량 LLM; 설정된 embedding |
| answer | 합성 PDF→준비된 구조화 근거→Wiki→심층 답변→수치·조건·인용·전문가 범위 검증 | 기본 LLM + embedding + reranker 모두 필요 |

`llm-light` 등 미설정 단계는 **미실행**으로 표시합니다. 잘못된 URL·인증·응답 형식·모델 출력은 **실패**입니다. `answer`는 검색 fallback이나 보류된 생성 결과를 성공으로 처리하지 않습니다. 경량 모델을 의도적으로 사용하지 않으면 `all` 대신 필요한 단계를 지정하세요.

각 단계는 독립성을 위해 합성 자료를 다시 준비합니다. `wiki`와 `answer`를 함께 고르면 Wiki 준비 호출도 각 단계에 포함됩니다. 최초에는 모든 단계를 반복하기보다 필요한 연결부터 확인하세요. 한 단계는 HTTP 시도 최대 80회이며 embedding의 기존 제한적 재시도도 포함합니다. LLM 생성 요청은 자동 재전송하지 않습니다. 모델·context·tokenizer·응답에 따라 Wiki 호출 수는 달라집니다. 최소 fixture 기반 점검이며 장문·대규모 코퍼스 부하 테스트는 아닙니다.

## 결과와 재실행

기본 결과 위치는 `artifacts/diagnostics/<UTC시각>-<실행ID>/`입니다. Git에서는 제외됩니다.

- `report.html`: 단계·항목별 성공/실패/미실행, 원인 안내, 모델별 HTTP 시도 횟수·입출력/전체 token·누적 응답 시간.
- `report.json`: 같은 결과를 자동화 도구에서 읽을 수 있는 구조화 기록. 입력·출력 token은 API의 `usage`만 사용합니다. 전체만 빠지고 입력·출력이 있으면 둘을 합산합니다.
- 토큰 미제공은 `null / 미제공`, 일부 응답만 확인되면 `일부`입니다. 실패·시간 초과 호출도 기록하며 확인되지 않은 token을 0으로 만들지 않습니다.
- HTTP 응답 수신 완료와 모델 출력 검증 성공은 다릅니다. HTTP 200 뒤 JSON 검증에서 실패해도 반환된 token은 기록합니다.
- 단계 시간에는 합성 입력 준비·검색·모델 대기를 모두 포함합니다. 응답 시간은 각 HTTP 전송 시도 이후 읽기 완료/오류까지의 합계입니다. 서버의 실제 과금 명세는 아닙니다.

~~~powershell
# 실패했던 단계만 재실행. API 호출에는 --live를 다시 지정합니다.
.\.venv\Scripts\python.exe -m backend.diagnostics --retry-from "artifacts/diagnostics/<실행폴더>/report.json" --live

# 보고서 위치 또는 단계 전체 제한 변경
.\.venv\Scripts\python.exe -m backend.diagnostics --steps wiki --live --output "C:\EvidenceReports" --stage-timeout 7200
~~~

기본 단계 전체 제한은 3,600초이며 `--stage-timeout`은 1~14,400초입니다. 개별 API timeout은 `.env`의 기존 설정을 따릅니다. 단계 제한/사용자 Ctrl+C 시 자식 프로세스를 중단하고 확인된 결과로 보고서를 남깁니다. 이미 사내 모델에 전송한 작업은 서버 측에서 계속 연산할 수 있습니다. 네트워크 대기 중 강제 중단된 호출의 최종 사용량은 확인할 수 없습니다.

종료 코드: **0=선택 단계 모두 성공**, **1=실패 있음**, **2=실패는 없지만 미실행 있음**. `--retry-from`은 실패한 단계만 선택합니다. 미설정으로 미실행된 항목은 설정 후 `--steps`로 직접 실행하세요.

## 운영 자료와의 분리

각 단계는 새로운 자식 프로세스와 임시 폴더를 사용합니다. `EVIDENCE_DB_PATH`를 임시 DB로 강제 지정하고 Wiki도 그 DB 옆에 생성합니다. 실제 사번·SSO 쿠키 대신 테스트용 로컬 계정을 사용하며 설정 파일은 읽기만 합니다. 폴더 감시·정기 worker는 실행하지 않습니다. 단계 종료 시 임시 PDF·DB·Wiki를 정리하고 보고서만 보존합니다.

추가로 자식 Python 프로세스의 파일 쓰기·삭제·이동과 SQLite 연결이 임시 폴더 밖으로 나가지 않게 검사합니다. 오프라인 단계의 외부 socket 연결도 차단합니다(단위 테스트용 loopback 서버는 허용). 이는 운영 자료를 잘못 건드리는 실수를 방지하는 방어이며, 임의의 악성 코드를 실행하는 보안 sandbox를 대체하지 않습니다.

API에는 코드에 포함된 짧은 합성 문장과 합성 논문만 전송합니다. 운영 논문·Wiki·질문 이력은 읽거나 복사하지 않습니다. 보고서에 키·API 주소·SSO 사번·provider 응답 원문을 저장하지 않으며, 모델명은 사용량 확인을 위해 표시합니다. 운영 DB·Wiki는 격리되지만 실제 모델 서버의 처리 용량은 운영 앱과 공유하므로 한가한 시간에 순차 점검하세요.

## 검증의 한계

현재 자동 OCR, canonical claim/수치/조건의 LLM 자동 저장, SSO 쿠키 검증·프로필 API는 미구현 연결부이므로 이 도구가 검증했다고 표시하지 않습니다. `curation`과 `answer`의 claim은 정답을 아는 JSON으로 가져옵니다. LLM 단순 추출 점검은 해당 생산 파이프라인의 구현을 의미하지 않습니다.

답변 문장 전체 일치 대신 인용 원문·담당 범위·parameter·수치·조건·추정 구분을 검사합니다. 한국어/영어의 대표적인 숫자·단위 표기를 지원하지만 다른 올바른 표현이 엄격한 검사를 통과하지 못할 수 있습니다. 실제 논문 품질은 별도 사내 평가 질문과 전문가 검토로 확인하세요. 프런트엔드 테스트는 기존 `node --test tests/*.test.mjs`로 실행하며 이 Python 도구에 포함하지 않습니다.
