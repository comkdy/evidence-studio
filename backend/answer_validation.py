"""Answer-only grounding guards, deliberately separate from Wiki publication.

These checks reject missing provenance and invented numeric literals. They do
not establish scientific truth or prove that a cited experiment is comparable.
"""
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from .wiki_engine import validate_sections, escaped
from .wiki_store import WikiError

KINDS = {'finding': '논문 근거', 'synthesis': '종합 해석',
         'hypothesis': '검증 전 가설', 'proposal': '제안 실험'}
HEADINGS = {'종합 판단', '논문 근거', '근거와 해석', '조건과 차이', '상충 결과',
            '적용 한계', '불확실성', '검증 전 가설', '제안 실험', '추가 검증'}
NUMBER = re.compile(r'(?<![A-Za-z0-9_])[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?')
SUPER = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻', '0123456789+-')
UNITS = {'s': ('time', '1'), 'sec': ('time', '1'), 'second': ('time', '1'), 'seconds': ('time', '1'),
         'ms': ('time', '.001'), 'us': ('time', '.000001'), 'µs': ('time', '.000001'), 'μs': ('time', '.000001'),
         'ns': ('time', '.000000001'), 'ps': ('time', '.000000000001'),
         'V': ('voltage', '1'), 'mV': ('voltage', '.001'), 'A': ('current', '1'), 'mA': ('current', '.001'),
         'µA': ('current', '.000001'), 'μA': ('current', '.000001'), 'nA': ('current', '.000000001'),
         'J': ('energy', '1'), 'pJ': ('energy', '1e-12'), 'fJ': ('energy', '1e-15'),
         'W': ('power', '1'), 'mW': ('power', '.001'), 'µW': ('power', '1e-6'),
         'C': ('celsius', '1'), '°C': ('celsius', '1'), '℃': ('celsius', '1'),
         '%': ('percent', '1'), 'percent': ('percent', '1'), 'nm': ('length', '1e-9'),
         'µm': ('length', '1e-6'), 'μm': ('length', '1e-6'), 'mm': ('length', '.001')}
QUANTITY = re.compile('('+NUMBER.pattern+r')\s*('+ '|'.join(re.escape(k) for k in sorted(UNITS, key=len, reverse=True))+r')(?![A-Za-z])')


def _numeric_text(text):
    text = str(text).replace('−', '-').replace(',', '')
    text = re.sub(r'10([⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+)', lambda m: '10^'+m[1].translate(SUPER), text)
    text = re.sub(r'([+\-]?(?:\d+(?:\.\d*)?|\.\d+))\s*[×x*·]\s*10\^\s*\{?([+\-]?\d+)\}?', r'\1e\2', text)
    text = re.sub(r'(?<![\w])10\^\s*\{?([+\-]?\d+)\}?', r'1e\1', text)
    return text


def numbers(text):
    values = set()
    text = _numeric_text(text)
    for match in NUMBER.finditer(text):
        try:
            value = Decimal(match[0])
            if value.is_finite():
                values.add(value)
        except InvalidOperation:
            pass
    return values


def quantities(text):
    result = set()
    for match in QUANTITY.finditer(_numeric_text(text)):
        dimension, factor = UNITS[match[2]]
        try:
            value = Decimal(match[1])
            # Keep pathological model exponents away from Decimal arithmetic.
            if value.is_finite() and abs(value.adjusted()) <= 1000:
                result.add((dimension, value * Decimal(factor)))
        except InvalidOperation:
            continue
    return result


def source_text(ev):
    if ev['kind'] == 'source_text':
        return ev['text']
    c = ev['claim']
    parts = [c['summary'], c.get('scope', '')]
    parts.extend(s['quote'] for s in c.get('sources', []))
    for m in c.get('measurements', []):
        parts.extend(str(m.get(k, '')) for k in ('label', 'raw_value', 'unit', 'sample'))
        parts.extend(s['quote'] for s in m.get('sources', []))
        for condition in m.get('conditions', []):
            parts.extend((condition['name'], condition.get('raw_value') or ''))
    return '\n'.join(parts)


def grounded_sections(result, evidence):
    if not isinstance(result, dict):
        raise WikiError('종합 답변 형식을 확인해야 합니다.')
    checked = validate_sections(result, evidence, allow_empty=True)
    for original, section in zip(result['sections'], checked):
        # Model-written titles must not smuggle uncited conclusions past the
        # sentence-level validator/reviewer.
        if section['heading'] not in HEADINGS:
            section['heading'] = '근거와 해석'
        for raw, item in zip(original['statements'], section['statements']):
            kind = raw.get('kind', 'synthesis')
            if not isinstance(kind, str) or kind not in KINDS:
                raise WikiError('답변의 사실·해석·가설·제안 구분을 확인해야 합니다.')
            primary = [evidence[key] for key in item['evidence_ids']
                       if evidence[key]['kind'] in ('claim', 'source_text')]
            if not primary:
                raise WikiError('Wiki만 인용한 결론입니다. 연결된 논문 근거가 필요합니다.')
            for key in item['evidence_ids']:
                ev = evidence[key]
                if ev['kind'] == 'wiki' and not set(ev.get('support_ids', [])) & set(item['evidence_ids']):
                    raise WikiError('Wiki의 연결 근거를 함께 인용해야 합니다.')
            if kind in ('finding', 'synthesis'):
                sources = [source_text(ev) for ev in primary]
                available = set().union(*(numbers(text) for text in sources))
                if not numbers(item['text']).issubset(available):
                    raise WikiError('원천 근거에서 확인되지 않은 수치가 포함되어 답변을 보류했습니다.')
                available_quantities = set().union(*(quantities(text) for text in sources))
                if not quantities(item['text']).issubset(available_quantities):
                    raise WikiError('원천 근거와 수치·단위 조합이 일치하지 않아 답변을 보류했습니다.')
            item['kind'] = kind
    return checked


def render_answer(sections, evidence, run_id=None):
    lines = []
    for section in sections:
        lines += ['## '+escaped(section['heading']), '']
        for st in section['statements']:
            links = []
            for key in st['evidence_ids']:
                ev = evidence[key]
                if ev['kind'] == 'wiki':
                    target = '/api/pilot/wiki-files/'+quote(ev['id'], safe='')+'/markdown?revision='+str(ev['revision'])
                    label = ev['title']+' · v'+str(ev['revision'])
                elif ev['kind'] == 'claim':
                    base = '/api/pilot/answer-runs/'+quote(run_id, safe='')+'/claims/' if run_id else '/api/pilot/claims/'
                    target, label = base+quote(ev['claim']['id'], safe=''), ev['claim']['id']
                else:
                    target = '/api/pilot/documents/'+quote(ev['document_id'], safe='')+'/pdf#page='+str(ev['page_number'])
                    label = '원문 p. '+str(ev['page_number'])
                links.append('['+escaped(label)+']('+target+')')
            lines += ['- '+escaped(KINDS[st['kind']]+' — '+st['text'])+' ('+' · '.join(links)+')', '']
            for span in st['source_spans']:
                lines += ['  > '+escaped(span['quote']), '']
    return '\n'.join(lines)


def review_items(groups):
    return [{'id': f'{group}:{si}:{ti}', 'section_heading': section['heading'], **statement}
            for group, sections in groups.items()
            for si, section in enumerate(sections)
            for ti, statement in enumerate(section['statements'])]


def apply_review(groups, review):
    expected = {item['id'] for item in review_items(groups)}
    checks = review.get('checks')
    if not isinstance(checks, list) or len(checks) != len(expected):
        raise WikiError('심층 근거 검토가 모든 진술을 확인하지 못했습니다.')
    verdicts = {}
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get('id'), str) or check.get('id') not in expected or check['id'] in verdicts or check.get('verdict') not in ('supported', 'unsupported', 'uncertain'):
            raise WikiError('심층 근거 검토 형식을 확인해야 합니다.')
        if not isinstance(check.get('reason', ''), str) or len(check.get('reason', '')) > 600:
            raise WikiError('심층 검토 사유의 형식을 확인해야 합니다.')
        verdicts[check['id']] = check['verdict']
    # Removing isolated sentences can leave misleading conclusions; suppress the
    # entire answer group if any of its claims fails review.
    removed = []
    for group in groups:
        if any(v != 'supported' for k, v in verdicts.items() if k.startswith(group+':')):
            groups[group] = []
            removed.append(group)
    return removed
