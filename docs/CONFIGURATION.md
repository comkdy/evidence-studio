# 사내 연결과 모델 설정

실제 `.env`는 서버에서만 읽으며 Git에 포함하지 않습니다. `.env.example`을 처음 한 번 복사한 후 사내 값을 채우세요. 기존 `.env`를 템플릿으로 덮어쓰지 마세요. 변경 후 서버를 재시작합니다.

## 공유 연결

| 작업 | URL | API key | 모델 이름 |
| --- | --- | --- | --- |
| 기본 LLM: 종합·답변·검증·lint | `LLM_BASE_URL` | `LLM_API_KEY` | `LLM_BASE_MODEL` |
| 경량 LLM: 원문 사실·인용 추출 | `LLM_BASE_URL` | `LLM_API_KEY` | `LLM_LIGHT_MODEL` |
| Embedding | `EMBEDDING_BASE_URL` | `EMBEDDING_API_KEY` | `EMBEDDING_MODEL` |
| Reranker | `EMBEDDING_BASE_URL` | `EMBEDDING_API_KEY` | `RERANKER_MODEL` |

`LLM_LIGHT_MODEL`이 비어 있으면 기본 모델을 사용합니다. 경량 모델을 설정했을 때 기본/경량 모델은 같은 URL과 키를 사용하지만 context 한도와 tokenizer는 각각 설정할 수 있습니다.

LLM URL에는 기본 경로 또는 `/chat/completions` 전체 URL을 입력합니다. 기본 경로이면 `/chat/completions`를 덧붙입니다. Bearer 인증으로 `{model, messages}`를 보내고 `choices[0].message.content`를 읽습니다.

Embedding/Reranker URL은 **동일한 전체 호출 URL**입니다. 두 요청 모두 입력한 주소에 POST하며 `/rerank` 같은 경로를 추측해서 덧붙이지 않습니다. URL 공유가 요청·응답 형식까지 같다는 의미는 아닙니다.

- Embedding: `{input: text, model}` → `data[0].embedding`. 한 문자열씩 전송합니다.
- Reranker: `{model, query, documents}` → `results[{index, relevance_score}]` 또는 `[{index, score}]`.

현재 사내 Embedding 계약은 확인되었으며 Reranker의 실제 요청·응답 계약은 사내 연결 테스트로 확인해야 합니다. URL·모델·키를 프런트엔드에 노출하지 않습니다.

## .env와 JSON의 역할

이 프로젝트의 URL, API key, 모델 이름, context 한도, timeout, 동시 요청 수는 배포 환경과 사내 모델에 따라 달라지므로 환경변수/`.env`로 관리합니다. 기본값이 맞으면 선택 항목을 추가할 필요가 없습니다. `.env`보다 시스템 환경변수가 우선합니다.

JSON이 필수인 것은 아닙니다. 향후 작업별 생성 파라미터 조합이나 복잡한 모델 선택 규칙을 여러 버전으로 관리한다면 별도 JSON/YAML이 적합할 수 있습니다. 현재는 설정 출처를 더 늘리지 않습니다. 공통 작성 지침과 프롬프트는 기존 `backend/prompts/`의 Markdown·텍스트 파일로 관리합니다.

환경별 연결 설정을 코드와 분리하는 원칙은 [Twelve-Factor의 Config](https://12factor.net/config)를 참고했습니다. 이 앱의 설정 우선순위는 **시스템 환경변수 → .env → 코드 기본값**입니다. 빈 환경변수도 명시적인 설정으로 취급합니다.

## 선택적인 튜닝 항목

아래 값을 바꿀 필요가 있을 때만 `.env`에 추가합니다. 템플릿에서 항목을 줄였어도 기존 변수와 기본 동작은 계속 지원합니다.

| 설정 | 기본값 | 용도 |
| --- | --- | --- |
| `LLM_TIMEOUT_SECONDS` | `300` | 기본·경량 LLM의 1회 socket I/O 대기 한도 |
| `LLM_QUEUE_TIMEOUT_SECONDS` | `300` | 공유 LLM 호출 슬롯을 기다리는 한도 |
| `LLM_MAX_CONCURRENT_REQUESTS` | `1` | 한 서버 프로세스 전체의 동시 LLM 호출 상한; 자원 그룹별 한 슬롯 제한도 함께 적용 |
| `RESEARCH_TIMEOUT_SECONDS` | `1500` | 심층 분석의 새 작업 시작·결과 채택 예산 |
| `EMBEDDING_TIMEOUT_SECONDS` | `30` | Embedding 1회 I/O 대기 한도 |
| `RERANKER_TIMEOUT_SECONDS` | `30` | Reranker 1회 I/O 대기 한도 |
| `LLM_CONTEXT_TOKENS` | `16384` | 기본 모델의 입력+출력 context 한도 |
| `LLM_LIGHT_CONTEXT_TOKENS` | `16384` | 경량 모델의 입력+출력 context 한도 |
| `WIKI_OUTPUT_TOKENS` | `2048` | Wiki 요청에 예약하고 전달하는 출력 한도 |
| `EMBEDDING_MAX_INPUT_TOKENS` | `512` | Embedding 모델의 입력 한도 |
| `LLM_TOKENIZER_PATH` | 비어 있음 | 기본 LLM의 선택적 로컬 tokenizer.json |
| `LLM_LIGHT_TOKENIZER_PATH` | 비어 있음 | 경량 LLM의 선택적 로컬 tokenizer.json |
| `CHUNK_TOKENIZER_PATH` | 비어 있음 | Embedding 분할용 선택적 로컬 tokenizer.json |

context/input 한도는 실제 모델 규격에 맞춰야 합니다. 기본값이 사내 모델의 규격이라는 뜻은 아닙니다. tokenizer 파일은 필수가 아닙니다. LLM tokenizer가 없으면 UTF-8 byte 수를 보수적인 token 추정치로 사용하며, Embedding은 비교용 tokenizer를 사용합니다. 실제 서버의 token 수와 정확히 같지는 않습니다.

예를 들어 사내 LLM의 대기 시간을 늘릴 때는 아래 한 줄을 `.env`에 추가합니다. JSON이나 앱 코드를 수정할 필요가 없습니다.

```dotenv
LLM_TIMEOUT_SECONDS=600
```

상위 게이트웨이가 먼저 요청을 끊는 경우 해당 게이트웨이의 제한도 조정해야 합니다. 앱 설정은 게이트웨이 설정을 변경하지 않습니다.

## 기존 .env에서 변경할 부분

답변·배경 분석·독립 내용 검토의 모델은 각각 `LLM_ANSWER_MODEL`, `LLM_BACKGROUND_MODEL`, `LLM_REVIEW_MODEL`로 선택할 수 있습니다. 생략하거나 비우면 `LLM_BASE_MODEL`을 사용합니다. 역할별 URL·키는 분리하지 않으며 모두 `LLM_BASE_URL`·`LLM_API_KEY`를 공유합니다. 기존 수동 Wiki 경로의 `LLM_LIGHT_MODEL`도 유지합니다.

역할별 `_CONTEXT_TOKENS`, `_TOKENIZER_PATH`, `_MAX_OUTPUT_TOKENS`, `_RESOURCE_GROUP`, `_DEPLOYMENT_REVISION`을 선택적으로 지정할 수 있습니다. 출력 한도 0은 별도 역할 기본값을 두지 않는다는 뜻이며 작업에서 지정한 출력 한도가 우선합니다. 모델 배포 내용이 바뀌면 revision을 변경해 캐시가 이전 답변을 재사용하지 않도록 합니다.

`LLM_RESOURCE_GROUP` 기본값은 `shared`이며 모든 역할이 상속합니다. 모델 이름이나 URL이 달라도 같은 서버 자원을 사용하면 같은 그룹으로 둡니다. 그룹마다 한 호출만 진행하고, 호출 사이에 사용자 작업을 우선합니다. 실제 하드웨어 분리가 확인된 경우에만 역할의 그룹을 나누고 전체 동시 상한을 조정합니다. 토큰 무제한을 동시 처리 능력으로 해석하지 않습니다.

1. `LLM_MODEL`의 값을 `LLM_BASE_MODEL`로 옮깁니다. 기존 이름도 호환 별칭으로 계속 읽습니다.
2. `LLM_LIGHT_BASE_URL`, `LLM_LIGHT_API_KEY`를 삭제합니다. 경량 모델은 공통 LLM URL·키를 사용합니다.
3. `RERANKER_BASE_URL`을 삭제합니다. `EMBEDDING_BASE_URL`을 공통 전체 URL로 설정합니다.
4. 기존 모델별 context/출력 한도, timeout, 동시 호출 수 등 튜닝 값은 유지합니다.

공통 값과 동일한 이전 URL·키가 남아 있어도 작동합니다. 활성화한 모델의 이전 값이 공통 값과 다르면, 다른 서버로 조용히 전환하지 않고 해당 변수 이름을 명시한 설정 오류를 냅니다. 오류에 실제 URL·키 값은 출력하지 않습니다.

새/이전 기본 모델 이름이 같은 설정 출처에 모두 있으면 `LLM_BASE_MODEL`을 우선합니다. 시스템의 이전 `LLM_MODEL`도 파일의 모델 설정보다 우선합니다. 명시적인 빈 값은 이전 이름으로 되돌아가지 않습니다.

## 연결 점검

```powershell
.\.venv\Scripts\python.exe -m backend.diagnostics --steps llm,llm-light,embedding,reranker --live
```

미설정 경량 모델은 미실행으로 표시됩니다. 필요한 단계만 골라 실행해도 됩니다. 이 진단은 `.env`의 공유 연결을 실제로 사용하며, 운영 DB/Wiki 대신 격리된 임시 자료로 확인합니다. 실제 API 호출과 token 사용이 발생합니다. 오프라인 테스트와 결과 HTML은 [진단 안내](DIAGNOSTICS.md)를 참고하세요.
