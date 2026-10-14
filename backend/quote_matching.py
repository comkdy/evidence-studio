"""Conservative quotation matching with offsets into the unchanged source.

No edit distance, case folding, NFKC, number conversion, or unit conversion.
The fallback only handles enumerated typography differences. Ambiguous matches
remain errors; callers must never save the model's reformatted quotation.
"""
from __future__ import annotations

import re
import unicodedata


REASONS = {
    'quote_invalid': ('인용 형식 오류', '인용을 문자열로 제공하세요.'),
    'quote_empty': ('인용 누락', '해당 근거의 연속된 원문 구절을 인용하세요.'),
    'quote_too_short': ('인용 길이 부족', '위치를 확인할 수 있도록 앞뒤 문맥을 포함해 12자 이상 인용하세요.'),
    'quote_not_found': ('원문에서 인용을 찾지 못함', '수치·단위·부등호와 생략된 문구를 확인하세요. 요약·번역문은 직접 인용할 수 없습니다.'),
    'quote_ambiguous': ('인용 위치 중복', '같은 구절이 여러 곳에 있습니다. 앞뒤 문맥을 더 포함해 위치를 구분하세요.'),
    'quote_wrong_evidence': ('근거 ID 연결 오류', '다른 제공 근거에서 인용이 발견됐습니다. 해당 구절의 evidence ID를 확인하세요.'),
    'quote_boundary': ('근거 경계를 넘는 인용', '페이지 또는 원문 조각별로 인용을 나누고 각각의 evidence ID를 연결하세요.'),
}


class QuoteMatchError(ValueError):
    def __init__(self, code):
        self.code = code
        title, hint = REASONS[code]
        super().__init__(f'{title} [{code}]. {hint}')


LIGATURES = str.maketrans({'ﬀ': 'ff', 'ﬁ': 'fi', 'ﬂ': 'fl', 'ﬃ': 'ffi', 'ﬄ': 'ffl', 'ﬅ': 'st', 'ﬆ': 'st'})
# Only spacing is normalized here. Case, prefixes, micro symbols, powers, and
# temperature symbols remain distinct. Longest first prevents prefix matches.
UNITS = ('seconds', 'second', 'percent', 'sec', 'µm', 'μm', 'nm', 'mm', 'cm',
         'µs', 'μs', 'ms', 'ns', 'ps', 'fs', 'µA', 'μA', 'mA', 'nA', 'pA',
         'mV', 'kV', 'meV', 'eV', 'µW', 'μW', 'mW', 'pJ', 'fJ', '°C', '℃',
         'Hz', 'kHz', 'MHz', 'GHz', 'K', 's', 'V', 'A', 'J', 'W', '%')
UNIT_SPACE = re.compile(r'(?<=\d)\s+(?=(?:'+ '|'.join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))+r')(?![\w]))')
UNIT_END = re.compile(r'\d\s*(?:'+'|'.join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))+r')$')
WRAP = re.compile(r'(?<!\w)([A-Za-z]{2,})[-‐‑][ \t]*\r?\n[ \t]*([a-z]{2,})(?!\w)')
UNIT_WORDS = frozenset(UNITS) | {'mol', 'ampere', 'volt', 'watt', 'joule', 'kelvin'}


def _replace(units, pattern, replacement):
    """Each output character remembers its entire original character range."""
    text = ''.join(u[0] for u in units)
    output, cursor = [], 0
    for match in pattern.finditer(text):
        output.extend(units[cursor:match.start()])
        value = replacement(match)
        if value is None:
            output.extend(units[match.start():match.end()])
        else:
            output.extend((char, units[match.start()][1], units[match.end()-1][2]) for char in value)
        cursor = match.end()
    output.extend(units[cursor:])
    return output


def _normalize(text, *, typography=False, marker=None):
    units = [(c, i, i+1) for i, c in enumerate(text)]
    if typography:
        # Canonical decomposition, not compatibility decomposition: 10² stays
        # 10², H₂ stays H₂, µ stays µ. Accented letters keep raw offset mapping.
        clusters = []
        for c, lo, hi in units:
            if clusters and unicodedata.combining(c):
                value, start, _ = clusters[-1]
                clusters[-1] = (value+c, start, hi)
            else:
                clusters.append((c, lo, hi))
        units = [(part, lo, hi) for c, lo, hi in clusters
                 for part in unicodedata.normalize('NFD', c.translate(LIGATURES))]
        units = _replace(units, re.compile(r'(?<=[A-Za-z])[\u00ad\u200b\ufeff](?=[A-Za-z])'), lambda m: '')
        units = _replace(units, re.compile(r'(?<=[A-Za-z])[‐‑](?=[A-Za-z])'), lambda m: '-')
        units = _replace(units, re.compile(r'(?<=[A-Za-z])[‘’](?=[A-Za-z])'), lambda m: "'")
        units = _replace(units, re.compile('[“”]'), lambda m: '"')
        units = _replace(units, UNIT_SPACE, lambda m: '')
        if marker:
            # Mark only a physical line break inside a multi-letter word. Each
            # marked hyphen may be retained OR omitted independently, allowing
            # "require-\nment thin-\nfilm" -> "requirement thin-film".
            current = ''.join(u[0] for u in units)
            ranges = []
            for m in WRAP.finditer(current):
                if len(m[1]+m[2]) >= 6 and m[1] not in UNIT_WORDS and m[2] not in UNIT_WORDS:
                    ranges.append((m.end(1), m.start(2)))
            for lo, hi in reversed(ranges):
                units[lo:hi] = [(marker, units[lo][1], units[hi-1][2])]
    units = _replace(units, re.compile(r'\s+'), lambda m: ' ')
    return ''.join(u[0] for u in units), units


def _matches(text, units, pattern):
    matches, cursor = set(), 0
    while match := pattern.search(text, cursor):
        lo, hi = match.span()
        # Never match half of an expanded original character (e.g. f in ﬀ).
        partial_number = ((lo > 0 and text[lo].isdecimal() and
                           (text[lo-1].isalnum() or text[lo-1] in '._^+−-')) or
                          (hi < len(text) and text[hi-1].isdecimal() and
                           (text[hi].isalnum() or text[hi] in '_^'
                            or (text[hi] == '.' and hi+1 < len(text) and text[hi+1].isdecimal()))))
        partial_unit = hi < len(text) and text[hi].isalnum() and UNIT_END.search(text[lo:hi])
        if (not partial_number and not partial_unit
                and (lo == 0 or units[lo-1][1:3] != units[lo][1:3])
                and (hi == len(units) or units[hi-1][1:3] != units[hi][1:3])):
            matches.add((units[lo][1], units[hi-1][2]))
        if len(matches) > 1:
            raise QuoteMatchError('quote_ambiguous')
        cursor = lo+1
    return matches


def locate_quote(text, quote):
    """Return a unique raw [start, end) range; never return normalized text."""
    if not isinstance(quote, str):
        raise QuoteMatchError('quote_invalid')
    if not quote.strip():
        raise QuoteMatchError('quote_empty')
    wanted, _ = _normalize(quote.strip())
    normalized, units = _normalize(text)
    found = _matches(normalized, units, re.compile(re.escape(wanted)))
    if found:
        return next(iter(found))
    wanted, _ = _normalize(quote.strip(), typography=True)
    # The internal marker never aliases a source/quote character.
    marker = next((chr(n) for n in range(0xFDD0, 0xFDF0) if chr(n) not in text+quote), None)
    normalized, units = _normalize(text, typography=True, marker=marker)
    pattern = ''
    for i, char in enumerate(wanted):
        if marker and i and wanted[i-1].isascii() and wanted[i-1].isalpha() and char.isascii() and char.isalpha():
            pattern += re.escape(marker)+'?'
        pattern += '(?:'+re.escape('-')+'|'+re.escape(marker)+')' if char == '-' and marker else re.escape(char)
    found = _matches(normalized, units, re.compile(pattern))
    if not found:
        raise QuoteMatchError('quote_not_found')
    return next(iter(found))


def locate_evidence_quote(evidence, key, quote):
    """Diagnose an incorrect ID/boundary without silently widening its scope."""
    if isinstance(quote, str) and quote.strip() and len(quote.strip()) < 12:
        raise QuoteMatchError('quote_too_short')
    try:
        return locate_quote(evidence[key]['text'], quote)
    except QuoteMatchError as error:
        if error.code != 'quote_not_found':
            raise
    sources = [(k, e) for k, e in evidence.items() if e['kind'] == 'source_text']
    for other, ev in sources:
        if other == key:
            continue
        try:
            locate_quote(ev['text'], quote)
        except QuoteMatchError as error:
            if error.code != 'quote_ambiguous':
                continue
        raise QuoteMatchError('quote_wrong_evidence')
    sources.sort(key=lambda pair: (pair[1]['paper_id'], pair[1]['document_id'], pair[1]['page_number'], pair[1]['offset']))
    for (ka, a), (kb, b) in zip(sources, sources[1:]):
        if key not in (ka, kb) or (a['paper_id'], a['document_id']) != (b['paper_id'], b['document_id']):
            continue
        adjacent = (a['page_number'] == b['page_number'] and a['offset']+len(a['text']) == b['offset'])
        adjacent |= b['page_number'] == a['page_number']+1 and b['offset'] == 0
        if not adjacent:
            continue
        separator = '' if a['page_number'] == b['page_number'] else '\n'
        try:
            lo, hi = locate_quote(a['text']+separator+b['text'], quote)
            if lo < len(a['text']) and hi > len(a['text'])+len(separator):
                raise QuoteMatchError('quote_boundary')
        except QuoteMatchError as error:
            if error.code == 'quote_boundary':
                raise
    raise QuoteMatchError('quote_not_found')
