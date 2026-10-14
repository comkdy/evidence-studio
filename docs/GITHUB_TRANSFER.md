# GitHub로 Evidence Studio 이관하기

저장소: https://github.com/comkdy/evidence-studio

이 공개 저장소에는 앱 소스, 실행 빌드, 설정 양식과 소개 자료가 있습니다. 원본 PDF, 실제 DB·Wiki, 분석 결과·백업, 실제 .env와 API 키는 포함하지 않습니다. 공개 이력에도 이전 PDF 포함 로컬 커밋을 전송하지 않습니다.

## 사내에서 처음 받기

사내 PC에서 GitHub와 승인된 Python 패키지 저장소에 접근할 수 있어야 합니다. 공개 저장소는 clone할 때 GitHub 로그인이 필요하지 않습니다.

~~~powershell
git clone https://github.com/comkdy/evidence-studio.git
cd evidence-studio
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock.txt
Copy-Item .env.example .env
~~~

첫 실행 전 .env의 BOOTSTRAP_ADMIN_EMPLOYEE_ID에 실제 초기 관리자 사번을 지정합니다. 사번은 공개 파일에 넣지 않습니다. 비어 있으면 local_admin에서는 데모 ID 0000000을 사용하며, SSO 모드는 시작되지 않습니다.

.env에 사내 LLM·Embedding·Reranker의 주소·모델·키를 설정하고, 별도로 준비한 PDF를 raw 폴더 바로 아래에 넣습니다. 이미 작성한 .env는 다시 복사하지 않습니다.

~~~powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8011
~~~

http://127.0.0.1:8011 에 접속합니다. 서버가 처음 실행되면 DB schema와 기본 설정을 만들고, raw의 PDF를 수집하여 새 DB와 Wiki를 작성합니다. 이후 raw는 60초마다 확인합니다. dist를 포함하므로 화면을 수정하지 않는다면 Node 설치가 필요 없습니다.

현재 SSO 어댑터, claim·수치·실험 조건 자동 추출 등 추가 구현 범위는 [HANDOFF.md](HANDOFF.md)를 확인하세요. LLM 키만 설정하면 이 부분까지 모두 완성되는 것은 아닙니다.

## 이후 변경 받기

앱을 중지하고 프로젝트 폴더에서 실행합니다.

~~~powershell
git status
git pull --ff-only
~~~

사내 코드 수정이 있으면 해당 변경을 먼저 commit하거나 별도 branch로 관리하세요. .env·DB·Wiki·새 PDF는 Git에서 제외되므로 코드 갱신에 포함되지 않습니다. 의존성 파일이 변경되었다면 Python 패키지를 다시 설치하고 실행합니다.

## 자료와 빌드 관리

raw/, data/, wiki/에는 .gitkeep만 추적합니다. 실제 자료는 사내에서 별도로 보관하고 백업합니다. 공개 저장소에 사내 자료나 키를 강제로 추가하지 마세요.

프런트엔드를 수정하면 pnpm build 후 갱신된 dist도 함께 commit합니다. 팀 소개 자료는 [evidence-studio-introduction.html](evidence-studio-introduction.html)을 내려받아 브라우저로 열면 됩니다.
