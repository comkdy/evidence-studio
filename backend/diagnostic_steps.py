"""Small synthetic fixtures exercise production entry points, never user papers."""
import io
import json
import os
import re
import time
import unittest
from pathlib import Path

from .diagnostics import STEPS, LIVE_STEPS, ROOT
from .diagnostic_safety import DiagnosticFailure, HTTPMeter

QUOTE = 'The DRAM simulation estimated 25 percent refresh power reduction at 85 C.'
TEXT = ('Synthetic DRAM Diagnostic Study\n\nA. Example, B. Example\nDiagnostic Laboratory\n'
        'Abstract - This synthetic DRAM study evaluates refresh power in a simulation.\n'
        'The numbers are test fixtures and are not research findings.\nINTRODUCTION\n'
        'DRAM refresh consumes energy to preserve stored data.\nRESULTS\n'+QUOTE+'\n'
        'The baseline uses a fixed refresh interval. No device measurement was performed.\n'
        'The estimate applies only to this simulated array and temperature.\nCONCLUSION\n'
        'The power estimate must not be generalized to measured devices.')


def make_pdf(path, text=TEXT):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=612,height=792)
    font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    parts = ['BT /F1 10 Tf 30 750 Td']
    for line in text.splitlines():
        parts += ['('+line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')+') Tj','0 -16 Td']
    stream = DecodedStreamObject()
    stream.set_data(('\n'.join(parts+['ET'])).encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(stream)
    with path.open('wb') as out:writer.write(out)


class Context:
    def __init__(self, step, root, live):
        self.step, self.root, self.live = step, root, live
        self.db = root/'diagnostic.sqlite3'
        self.result = dict(step=step,title=STEPS[step][0],status='running',mode='실제 API' if live else '로컬',checks=[],calls=[])
        self.meter = None
        self.save()

    def save(self):
        if self.meter:self.result['calls'] = self.meter.calls
        path = self.root/'result.json.tmp'
        path.write_text(json.dumps(self.result,ensure_ascii=False),encoding='utf-8')
        path.replace(self.root/'result.json')

    def check(self, name, condition, detail=''):
        self.result['checks'].append(dict(name=name,status='passed' if condition else 'failed',detail=detail))
        self.save()
        if not condition:raise DiagnosticFailure(name+' 점검에 실패했습니다.')

    def require(self, service, label):
        if not (service.base_url and service.model and service.api_key.get_secret_value()):
            raise unittest.SkipTest(label+' 주소·모델·API key 설정이 필요합니다.')

    def prepare(self, *, curated=False):
        from .pilot_db import initialize, connect
        from .pilot_ingest import ingest_file, sha256_file
        from .pilot_curate import apply_curation
        initialize(self.db)
        pdf = self.root/'synthetic-dram.pdf'
        make_pdf(pdf)
        result = ingest_file(pdf,self.db,0)
        self.check('합성 PDF 원문 추출·적재',result['status']=='ready')
        if curated:
            payload = {'papers':[{'filename':pdf.name,'sha256':sha256_file(pdf),'experts':['dram'],'claims':[{
                'id':'diagnostic-claim','summary':QUOTE,'kind':'estimate','scope':'Simulation only; not a device measurement.',
                'sources':[{'page':1,'quote':QUOTE,'locator':'Results'}],
                'measurements':[{'id':'diagnostic-measurement','label':'Refresh power reduction','raw_value':'25%',
                    'operator':'=','numeric_value':25,'unit':'%','sample':'Simulated DRAM array','kind':'estimated',
                    'conditions':[{'name':'Temperature','raw_value':'85 C','source':{'page':1,'quote':QUOTE,'locator':'Results'}}]}]}]}], 'wikis':[]}
            source = self.root/'curation.json'
            source.write_text(json.dumps(payload),encoding='utf-8')
            outcome = apply_curation(source,self.db)
            self.check('준비된 claim·수치 JSON 저장',outcome['status']=='created','LLM 자동 추출 정확도를 검사하는 단계가 아닙니다.')
        with connect(self.db) as db:
            self.check('DB 참조 무결성',not db.execute('PRAGMA foreign_key_check').fetchall())
        return pdf, result['paper_id']


def unit(ctx):
    import sys
    sys.path.insert(0,str(ROOT/'tests'))
    suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
    result = unittest.TextTestRunner(stream=io.StringIO(),verbosity=0).run(suite)
    for test, _trace in result.errors + result.failures:
        ctx.result['checks'].append(dict(name=test.id(),status='failed',detail='회귀 테스트 실패. 해당 테스트를 별도로 실행해 확인하세요.'))
    for test, _reason in result.skipped:
        ctx.result['checks'].append(dict(name=test.id(),status='skipped',detail='테스트의 실행 조건이 충족되지 않았습니다.'))
    ctx.check('백엔드 테스트 실행',result.testsRun>0 and result.wasSuccessful(),
              f'{result.testsRun}개 실행 · 실패 {len(result.failures)} · 오류 {len(result.errors)} · 미실행 {len(result.skipped)}')
    if result.skipped:raise unittest.SkipTest('일부 테스트를 실행하지 못했습니다. 설치된 의존성과 실행 조건을 확인하세요.')


def ingest(ctx):
    from .pilot_db import connect
    from .pilot_ingest import ingest_file
    pdf, _ = ctx.prepare()
    with connect(ctx.db) as db:
        ctx.check('원문 문장 보존',QUOTE in db.execute('SELECT text FROM pages').fetchone()[0])
        ctx.check('검색 chunk 저장',db.execute('SELECT count(*) FROM search_chunks').fetchone()[0]>0)
    ctx.check('같은 PDF 중복 적재 방지',ingest_file(pdf,ctx.db,0)['status']=='unchanged')
    with connect(ctx.db) as db:ctx.check('논문 1편 유지',db.execute('SELECT count(*) FROM papers').fetchone()[0]==1)


def curation(ctx):
    from .pilot_db import connect
    from .pilot_api import search
    ctx.prepare(curated=True)
    with connect(ctx.db) as db:
        span = db.execute('SELECT e.*,p.text FROM evidence_spans e JOIN pages p ON p.id=e.page_id').fetchone()
        ctx.check('인용 offset·원문 일치',span['quote']==span['text'][span['start_offset']:span['end_offset']])
        ctx.check('Claim과 수치 연결',db.execute('SELECT count(*) FROM claim_measurements').fetchone()[0]==1)
        ctx.check('실험 조건 보존',db.execute('SELECT raw_value FROM condition_values').fetchone()[0]=='85 C')
    result = search('DRAM refresh power',expert='dram',limit=8)
    ctx.check('Parameter·수치 검색',bool(result['claims']) and result['claims'][0]['measurements'][0]['raw_value']=='25%')
    ctx.check('전문가 담당 범위 제한',not search('DRAM refresh power',expert='nor',limit=8)['claims'])


def chunking(ctx):
    from .chunking import load_chunk_policy, build_chunks
    policy = load_chunk_policy()
    if ctx.live and policy.mode!='semantic':raise unittest.SkipTest('실제 문맥 분할에는 embedding 설정과 semantic/auto 모드가 필요합니다.')
    page = dict(id='synthetic-page',page_number=1,text=TEXT,status='ready')
    audit = []
    chunks = build_chunks([page],'Synthetic DRAM Diagnostic Study',policy,diagnostics=audit)
    children = [child for parent in chunks for child in parent['children']]
    ctx.check('문맥 chunk 생성',bool(children),f'{policy.mode} · {len(children)}개 chunk')
    ctx.check('원문 위치와 chunk 일치',all(TEXT[s['start_offset']:s['end_offset']]==c['text'][s['chunk_start']:s['chunk_end']] for c in children for s in c['spans']))
    covered = {i for c in children for s in c['spans'] for i in range(s['start_offset'],s['end_offset'])}
    begin = TEXT.index(QUOTE)
    ctx.check('핵심 문장 누락 없음',all(i in covered for i in range(begin,begin+len(QUOTE)) if not TEXT[i].isspace()))
    ctx.check('모델 입력 한도 준수',all(len(policy.splitter(policy.max_tokens).chunks(c['context_text']))==1 for c in children))
    if ctx.live:ctx.check('Embedding 기반 경계 분석 실행',bool(audit) and any(c['service']=='embedding' for c in ctx.meter.calls))


def llm(ctx, light=False):
    from .ai_settings import load_ai_settings
    from .llm_client import CompanyLLM
    settings = load_ai_settings()
    service = settings.light_llm if light else settings.llm
    if service is None:raise unittest.SkipTest('선택 사항인 LLM_LIGHT_MODEL이 설정되지 않았습니다.')
    ctx.require(service,'경량 LLM' if light else 'LLM')
    result = CompanyLLM(service,limits=settings.limits).complete_json(
        'Return only a JSON object with keys parameter, value, unit, temperature_c, evidence_type. Extract exactly from the supplied synthetic sentence. Do not add facts.',
        {'text':QUOTE},max_output_tokens=256)
    ctx.check('JSON 응답 계약',isinstance(result,dict) and all(k in result for k in ('parameter','value','unit','temperature_c','evidence_type')))
    ctx.check('Parameter 의미 보존',bool(re.search('refresh|리프레시',str(result['parameter']),re.I)) and bool(re.search('power|전력',str(result['parameter']),re.I)))
    ctx.check('합성 수치·조건 추출',str(result['value']) in ('25','25.0') and str(result['temperature_c']) in ('85','85.0') and result['unit'] in ('%','percent'))
    ctx.check('측정과 추정 구분',bool(re.search('simulat|estimat',str(result['evidence_type']),re.I)))


def embedding(ctx):
    from .chunking import load_chunk_policy
    from .embedding_client import EmbeddingClient
    policy = load_chunk_policy()
    if not policy.embedding:raise unittest.SkipTest('Embedding 설정이 필요합니다.')
    client = EmbeddingClient(policy.embedding)
    vectors = client.embed(['DRAM refresh power consumption.','A recipe for baking bread.'])
    ctx.check('벡터 수·차원 일치',len(vectors)==2 and len(vectors[0])==len(vectors[1]),f'{len(vectors[0])}차원')
    similarity = sum(a*b for a,b in zip(*vectors))
    ctx.check('서로 다른 문장의 벡터 구분',similarity < .99999,'단순 smoke check이며 검색 품질 전체 평가가 아닙니다.')


def reranker(ctx):
    from .ai_settings import load_ai_settings
    from .wiki_retrieval import rerank
    service = load_ai_settings().reranker
    ctx.require(service,'Reranker')
    items = [dict(id='unrelated',text='A recipe for baking bread.'),dict(id='relevant',text='DRAM refresh power consumption is reduced in a simulation.')]
    result = rerank('DRAM refresh power reduction',items,service)
    ctx.check('순위 응답·문서 연결',{r['id'] for r in result}=={'unrelated','relevant'})
    ctx.check('관련 문서 우선 배치',result[0]['id']=='relevant','간단한 관련성 smoke check입니다.')


def compile_wiki(ctx, llm):
    from .wiki_engine import run_jobs
    from .wiki_store import WikiStore
    result = run_jobs(ctx.db,llm)
    ctx.check('Wiki job 완료',result.get('status') not in ('failed','busy','deferred'),result.get('error',''))
    store = WikiStore(ctx.db)
    rows = store.rows()
    ctx.check('Markdown 원천·공통 Wiki 생성',any(r['kind']=='source' for r in rows) and any(r['kind']=='topic' and r['status']=='ready' for r in rows))
    for row in rows:store.read(row)
    ctx.check('Wiki 파일·게시 hash 일치',True)
    return store


def wiki(ctx):
    from .llm_client import CompanyLLM
    from .pilot_db import connect
    from .pilot_ingest import ingest_file
    from .wiki_engine import enqueue, snapshot
    from .wiki_api import request_topic, TopicRequest
    from .wiki_lint import validate_grounding
    client = CompanyLLM()
    ctx.require(client.service,'LLM')
    _, pid = ctx.prepare()
    store = compile_wiki(ctx,client)
    source = store.record('source-'+pid)
    refs = json.loads(source['refs_json'])
    ctx.check('LLM 원문 인용 연결',bool(refs['source_spans']))
    before = len(ctx.meter.calls)
    with connect(ctx.db) as db:enqueue(db,scope='common');db.commit()
    compile_wiki(ctx,client)
    ctx.check('변경 없는 Wiki 재사용',len(ctx.meter.calls)==before,'API 재호출 없이 기존 결과를 재사용해야 합니다.')
    note = request_topic('dram',TopicRequest(title='DRAM refresh simulation limitations',source_ids=[source['id']]))
    compile_wiki(ctx,client)
    ctx.check('전문가 특화 Wiki 생성',store.record(note['id'])['kind']=='specialization')
    second = ctx.root/'new-dram.pdf'
    make_pdf(second,TEXT.replace('25 percent','35 percent').replace('85 C','95 C'))
    added = ingest_file(second,ctx.db,0)
    ctx.check('신규 논문 적재',added['status']=='ready')
    compile_wiki(ctx,client)
    ctx.check('기존 원천 Wiki 보존',store.record(source['id'])['content_hash']==source['content_hash'])
    ctx.check('신규 원천 Wiki 반영',store.record('source-'+added['paper_id']) is not None)
    ctx.check('공통 주제에 신규 논문 연결',any(r['kind']=='topic' and r['scope']=='common' and {pid,added['paper_id']}.issubset(json.loads(r['refs_json'])['paper_ids']) for r in store.rows()))
    ctx.check('전문가 목차에 신규 논문 연결',{pid,added['paper_id']}.issubset(json.loads(store.record('expert-index-dram')['refs_json'])['paper_ids']))
    state = snapshot(ctx.db);catalogue = {r['id']:r for r in store.rows()}
    problems = []
    for row in catalogue.values():
        if row['status']=='archived':continue
        _meta,body = store.read(row)
        problems += validate_grounding(row,body,state,catalogue,store)
    ctx.check('구조 lint·인용·의존 관계',not problems,f'{len(problems)}개 구조 문제')


def answer(ctx):
    from .ai_settings import load_ai_settings
    from .llm_client import CompanyLLM
    from .wiki_api import Question, answer as ask
    from .answer_trace import read_run
    from .wiki_retrieval import retrieve
    settings = load_ai_settings()
    for service,label in ((settings.llm,'LLM'),(settings.embedding,'Embedding'),(settings.reranker,'Reranker')):ctx.require(service,label)
    _, pid = ctx.prepare(curated=True)
    compile_wiki(ctx,CompanyLLM())
    result = ask(Question(question='What refresh power reduction does the DRAM simulation estimate, at what temperature, and was it measured?',expert_ids=['dram'],mode='deep'),CompanyLLM())
    ctx.check('근거 fallback 없이 답변 생성',result['synthesis']['mode']=='llm',result['synthesis']['notice'] if result['synthesis']['mode']!='llm' else '')
    ctx.check('자동 근거 검토 통과',result['research']['validation_status']=='model_reviewed')
    run = read_run(result['run_id'])
    ctx.check('계획·답변·검토 호출 정상 완료',all(c['status']=='completed' for c in run['trace']['model_calls']))
    ctx.check('Claim·parameter·수치 검색',bool(result['claims']) and any(m['label']=='Refresh power reduction' and m['raw_value']=='25%' for c in result['claims'] for m in c['measurements']))
    sections = run['trace']['published_sections']['synthesis']
    statements = [st for section in sections for st in section['statements']]
    text = ' '.join(st['text'] for st in statements)
    ctx.check('답변에 수치·조건 포함',bool(re.search(r'25\s*(?:%|percent|퍼센트)',text,re.I)) and bool(re.search(r'85\s*(?:°\s*)?(?:C|℃|degrees?\s*C|도)',text,re.I)))
    ctx.check('답변에서 추정임을 구분',bool(re.search('simulat|estimat|추정|시뮬레이션',text,re.I)))
    cited = {key for st in statements for key in st['evidence_ids']}
    ctx.check('답변 인용의 담당 논문 연결',bool(cited) and all(set(run['evidence'][key]['paper_ids'])=={pid} for key in cited))
    notices = result['synthesis']['notice']
    ctx.check('검색 서비스 fallback 없음',not any(word in notices for word in ('Embedding','Reranker','vector 색인')))
    outside, _ = retrieve('DRAM refresh power',['nor'],ctx.db)
    ctx.check('다른 전문가로 근거 유출 방지',not outside)


def execute(step, root, live):
    ctx = Context(step,root,live)
    started = time.monotonic()
    try:
        if step in LIVE_STEPS and not live:raise unittest.SkipTest('실제 API를 호출하는 단계입니다. 실행하려면 --live를 지정하세요.')
        if step=='unit':unit(ctx)
        else:
            from .ai_settings import load_ai_settings
            from .chunking import load_chunk_policy
            settings = load_ai_settings()
            routes = {}
            for name,service in [('llm',settings.llm),('llm-light',settings.light_llm),('reranker',settings.reranker)]:
                if service and service.base_url and service.model:
                    url = service.base_url.rstrip('/') if name.startswith('llm') else service.base_url
                    if name.startswith('llm') and not url.endswith('/chat/completions'):url += '/chat/completions'
                    routes[url,service.model] = name
            if step in ('embedding','chunking','wiki','answer'):
                policy = load_chunk_policy()
                if policy.embedding:routes[policy.embedding.endpoint,policy.embedding.service.model] = 'embedding'
            with HTTPMeter(routes,ctx.save) as meter:
                ctx.meter = meter
                functions = dict(ingest=ingest,curation=curation,chunking=chunking,llm=llm,embedding=embedding,reranker=reranker,wiki=wiki,answer=answer)
                if step=='llm-light':llm(ctx,light=True)
                else:functions[step](ctx)
        ctx.result.update(status='passed',summary='선택한 단계의 점검 항목을 통과했습니다.')
    except unittest.SkipTest as error:
        ctx.result.update(status='skipped',summary=str(error))
    except DiagnosticFailure as error:
        ctx.result.update(status='failed',summary=str(error))
    except ModuleNotFoundError:
        ctx.result.update(status='failed',summary='Python 의존성이 없습니다. backend/requirements-dev.txt 설치 후 다시 실행하세요.')
    except Exception as error:
        # Never serialize arbitrary provider exception text, URLs, keys or bodies.
        from .llm_client import LLMError
        from .embedding_client import EmbeddingError
        from .wiki_store import WikiError
        if isinstance(error,(LLMError,EmbeddingError,WikiError)):
            summary = str(error)  # These production exceptions contain only safe, locally authored messages.
        elif isinstance(error,PermissionError):
            summary = '격리 폴더 밖의 파일/DB 작업 또는 허용되지 않은 연결을 차단했습니다.'
        else:summary = '처리 중 오류가 발생했습니다 ('+type(error).__name__+'). 설정·응답 계약·입력 한도와 아래 호출 상태를 확인하세요.'
        ctx.result.update(status='failed',summary=summary)
    ctx.result['elapsed_ms'] = round((time.monotonic()-started)*1000)
    ctx.save()
    return 1 if ctx.result['status']=='failed' else 0
