"""Local, context-aware Wiki packing. Never calls an embedding or LLM service."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from semantic_text_splitter import TextSplitter

from .ai_settings import load_ai_settings, ENV_FILE
from .wiki_store import WikiError


@lru_cache(maxsize=8)
def read_tokenizer(path, stamp, size):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    data['truncation'] = None
    data['padding'] = None
    return json.dumps(data)


class WikiBudget:
    def __init__(self, context_tokens=16384, output_tokens=2048, tokenizer_path='', model=''):
        self.context_tokens = context_tokens
        self.output_tokens = output_tokens
        self.input_tokens = context_tokens - output_tokens - 512  # chat template/special-token reserve
        self.model = model
        self.tokenizer = ''
        if tokenizer_path:
            path = Path(tokenizer_path)
            if not path.is_absolute():
                path = ENV_FILE.parent / path
            try:
                stat = path.stat()
                self.tokenizer = read_tokenizer(str(path), stat.st_mtime_ns, stat.st_size)
            except (OSError, ValueError):
                raise WikiError('LLM tokenizer.json을 읽을 수 없습니다. LLM_TOKENIZER_PATH 설정을 확인하세요.') from None
        self.limit = self.splitter(self.input_tokens)

    def splitter(self, capacity):
        try:
            if self.tokenizer:
                return TextSplitter.from_huggingface_tokenizer_str(self.tokenizer, capacity, trim=False)
            # Conservative byte estimate, NOT a measurement of the company's tokenizer.
            # Avoid assuming that English, Korean and equations share chars/token ratios.
            return TextSplitter.from_callback(lambda text: len(text.encode('utf-8')), capacity, trim=False)
        except Exception:
            raise WikiError('LLM tokenizer 또는 Wiki 입력 한도 설정이 올바르지 않습니다.') from None

    def fits(self, system, payload):
        wire = json.dumps({'model': self.model, 'messages': [
            {'role':'system','content':system},
            {'role':'user','content':json.dumps(payload, ensure_ascii=False)}],
            'max_tokens':self.output_tokens}, ensure_ascii=False)
        return len(self.limit.chunks(wire)) <= 1

    def metadata(self):
        return {'context_tokens':self.context_tokens, 'output_tokens':self.output_tokens,
                'counter':('local-tokenizer:'+hashlib.sha256(self.tokenizer.encode()).hexdigest()
                           if self.tokenizer else 'conservative-utf8-bytes'), 'version':'wiki-context-v1'}


def budget_for(llm):
    if hasattr(llm, '_wiki_budget'):
        return llm._wiki_budget
    settings = load_ai_settings()
    service = llm.service
    llm._wiki_budget = WikiBudget(getattr(service, 'context_tokens', 16384), settings.wiki_output_tokens,
                                 getattr(service, 'tokenizer_path', ''), service.model)
    return llm._wiki_budget


def extraction_model(llm):
    from .llm_client import CompanyLLM
    if not isinstance(llm, CompanyLLM):
        return getattr(llm, '_wiki_extractor', llm)  # injected local test implementations
    if not hasattr(llm, '_wiki_extractor'):
        service = load_ai_settings().light_llm
        llm._wiki_extractor = CompanyLLM(service, limits=llm.limits) if service else llm
    return llm._wiki_extractor


def page_evidence(paper, low=0, high=None):
    """A joined reading stream, with each evidence item retaining original page offsets."""
    text, maps = '', []
    for page in paper['pages']:
        if page['status'] != 'ready' or not page['text'].strip():
            continue
        if text:
            text += '\n\n'
        maps.append((len(text), len(text)+len(page['text']), page))
        text += page['text']
    high = len(text) if high is None else high
    evidence = {}
    for start, end, page in maps:
        left, right = max(start, low), min(end, high)
        if left >= right:
            continue
        offset = left-start
        key = 'raw-'+str(page['id'])+'-'+str(offset)+'-'+str(right-start)
        evidence[key] = dict(kind='source_text', paper_id=paper['id'], page_id=page['id'],
            document_id=page['document_id'], page_number=page['page_number'], offset=offset,
            text=page['text'][offset:right-start])
    return text, evidence


def source_batches(paper, budget, fits):
    """Prefer paragraph/sentence boundaries across pages; split only when necessary.

    Recheck the entire serialized request (instructions, metadata, quotes included).
    No overlap or silent clipping: all original page characters are represented.
    """
    text, all_evidence = page_evidence(paper)
    if fits(all_evidence):
        return [all_evidence]
    capacity = budget.input_tokens
    while capacity >= 32:
        pieces = budget.splitter(capacity).chunk_indices(text)
        batches = [page_evidence(paper, offset, offset+len(piece))[1] for offset, piece in pieces]
        batches = [batch for batch in batches if batch]
        if batches and all(fits(batch) for batch in batches):
            return batches
        capacity = int(capacity*.75)
    raise WikiError('작성 지침·근거 metadata가 모델 입력 한도를 초과합니다. LLM_CONTEXT_TOKENS/tokenizer 설정을 확인하세요.')
