# 연구 질의 검색 평가

`backend.evaluate_research`는 같은 corpus에서 `raw_only`(원문), `structured`(원문+claim), `with_wiki`(원문+claim+Wiki)의 검색과 근거 조립을 비교한다. **검색 평가이며 과학적 답변 평가가 아니다.** 답변 LLM을 호출하거나 실험 방향의 타당성을 판정하지 않는다.

기본 실행에는 외부 모델 호출이 없다. DB를 읽기 전용으로 열고, 기존 색인만 사용한다. 초기화, 재색인, embedding 캐시 저장, 답변 이력 저장, Wiki 생성은 하지 않는다. 결과는 stdout으로 출력하며 `--output`을 지정한 경우 그 보고서 파일만 쓴다.

```powershell
.venv\Scripts\python.exe -m backend.evaluate_research --cases team-research-cases.json
.venv\Scripts\python.exe -m backend.evaluate_research --cases team-research-cases.json --database data\evidence-studio.sqlite3 --budget 32000 --top-k 30 --output artifacts\research-evaluation.json
```

`--allow-models`를 명시하면 설정된 embedding과 reranker를 호출할 수 있다. 질문 embedding은 case당 한 번 생성하여 세 조건에서 재사용한다. 색인 vector의 모델 fingerprint가 맞는 것만 사용하고, 각 조건에 같은 reranker를 적용한다. 답변 LLM은 이 옵션을 사용해도 호출하지 않는다. 요청 실패나 해당 vector 부재는 보고서 notices에 남는다. API 키나 모델 서비스 주소는 보고서에 넣지 않는다.

각 ablation은 유형을 먼저 제한한 뒤 후보를 검색하므로, 원문 후보가 Wiki에 밀려 상위 후보에서 미리 빠지는 비교 편향을 피한다. 검색은 현재 서비스와 같은 키워드 정규화, BM25, RRF와 공통 근거 조립기를 사용한다. 답변 서비스의 질문 분해·후속 검색·심층 검토는 평가하지 않는다. corpus fingerprint가 실행 중 달라지면 실패로 표시하고 결과를 다시 측정해야 한다.

## 입력과 정답

입력은 `cases` 배열을 갖는 JSON이다. 각 case에는 `id`, `question`, `expert_ids`가 필요하다. 중복 ID, 알 수 없는/중지된 전문가, DB에 없는 정답 ID는 오류다. `expected_paper_ids`는 필요한 원천 논문 목록이다. `expected_reference_ids`는 `passage:<reference_id>`, `claim:<claim_id>`, `wiki:<wiki_id>` 형태의 배열이거나 ablation 이름별 배열 객체다.

`shared/research_eval.example.json`은 **test-demo 합성 형식 예시**다. placeholder ID를 그대로 실행하면 invalid_case가 나오며 실제 DB 평가 결과로 사용할 수 없다. 전문가가 원문을 읽고 확인한 논문·근거 ID로 바꿔 사용한다. 임의로 모델이 만든 정답을 품질 검증 기준으로 삼지 않는다. 동일 근거가 원문과 claim에서 서로 다른 ID를 가지므로, 논문 recall을 공통 비교 지표로 사용하고 reference 정답은 필요하면 조건별로 지정한다.

정답 배열을 생략하거나 빈 배열로 두면 해당 recall은 `null`이다. 이는 100% 통과나 '정답 없음' 판정이 아니다. 그러한 질문은 근거 개수·예산·지연만 관측한다. 근거가 없는 질문에 적절히 답변을 거절하는지는 별도의 답변 평가에서 확인한다.

## 보고서 해석

| 항목 | 의미 |
|---|---|
| candidate/selected paper recall | 정답 논문 중 후보/최종 근거 패킷에 들어온 비율 |
| selected reference recall | 지정한 근거 ID 중 최종 패킷에 들어온 비율; Wiki가 확장한 claim도 포함 |
| selected paper diversity | 최종 패킷의 서로 다른 논문 수; 많다고 반드시 좋다는 뜻은 아님 |
| evidence chars / budget utilization | 조건·출처를 포함한 직렬화 문자 수와 한도 사용 비율 |
| grounded wiki coverage | 선택 Wiki 중 원천 support ID가 함께 있는 비율; 과학적 함의 검증과 다름 |
| retrieval/total ms | 해당 조건의 검색/근거 조립까지의 시간; 공통 질문 embedding 시간은 별도 표시 |
| failures / dropped units | 정답 누락, 입력 오류, 실행 중 corpus 변경 및 제외된 근거와 이유 |

`summary`의 recall은 정답이 지정된 case만의 단순 평균이다. 전체 실행의 모델 요청 수를 종류별로 기록하며, 답변 요청 수는 항상 0이다. CLI 종료 코드는 기대 근거 누락·유효하지 않은 case·실행 중 corpus 변경이 있으면 1, 입력/실행 오류면 2, 그 외에는 0이다. 0은 과학적 답변 품질 인증이 아니다.

## Wiki 효용 판별

팀 질문을 정확 수치, 조건이 다른 비교, 논문 간 상충, 메커니즘 설명, 다음 실험 설계, 확보 자료로 답할 수 없는 질문으로 나누어 준비한다. 먼저 이 도구로 동일 corpus·검색 설정·예산에서 원천 근거 recall과 Wiki 추가 비용을 비교한다. 통제된 반복 실행으로 지연의 변동을 확인한다.

다음 단계에서는 세 조건의 최종 패킷을 같은 답변 모델과 같은 출력 기준에 넣고, 분야 전문가가 방법을 모른 채 평가한다. 평가 기준은 조건 비교의 타당성, 누락된 반례, 직접 근거와 해석의 구분, 실험 가설·변수·통제군·관측 결과에 따른 판단의 유용성, 적절한 미응답이다. 이 CLI는 그 답변 실험이나 자동 점수를 구현했다고 주장하지 않는다. Wiki가 반복되는 종합 질문의 품질이나 비용에 이득을 보인 주제부터 확대한다.
