"""Versioned representative terms and synonyms; editorial rules, not evidence."""
import hashlib
import json
import re
import unicodedata
from .pilot_db import ROOT, connect, now


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def equivalent(text):
    # Matching only. Never replace stored scientific typography or source text.
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', text).translate(
        str.maketrans({'−':'-', '–':'-', '‐':'-'}))).strip().casefold()


def pure_unit(text):
    value=equivalent(text).replace('μ','u').replace('µ','u').replace('ω','ohm').replace('°c','celsius').replace('%','percent')
    parts=re.findall(r'[a-z]+',value)
    units=set('v mv uv kv vs a ma ua na pa w mw uw kw hz khz mhz ghz s ms us ns ps fs m cm mm um nm pm g kg k c celsius ohm kohm mohm f pf nf uf h j ev mev kev bit bits byte bytes b kb mb gb tb cycle cycles percent db mol rad sr tops ops flops'.split())
    residue=re.sub(r'[a-z]+','',value)
    return bool(parts) and all(p in units for p in parts) and not re.sub(r'[\s\d./·⋅×*^()+\-]','',residue)


def words(text):
    stop={'what','is','the','a','an','of','in','at','for','and','with','how','does','to'}
    return list(dict.fromkeys(w for w in re.findall(r'[a-z0-9]+|[가-힣]{2,}',equivalent(text)) if w not in stop))


def public(row):
    item=dict(row)
    item['aliases']=json.loads(item.pop('aliases_json'))
    item['related']=json.loads(item.pop('related_json'))
    return item


def audit(db, item, actor):
    db.execute('INSERT INTO terminology_revisions VALUES(?,?,?,?,?)',
        (item['id'],item['revision'],encoded(item),actor,item['updated_at']))


def seed(db):
    if db.execute('SELECT seeded FROM terminology_meta WHERE id=1').fetchone()[0]:
        return
    items=json.loads((ROOT/'shared/terminology.json').read_text(encoding='utf-8'))['terms']
    extra={'리텐션':'retention','전력':'power','전류':'current','수명':'lifetime','지연':'latency',
        '온도':'temperature','누설':'leakage','추정':'estimated','시뮬레이션':'simulation',
        '강유전':'ferroelectric','분극':'polarization','임프린트':'imprint','웨이크업':'wakeup',
        '피로':'fatigue','산소 공공':'oxygen vacancy','접촉 저항':'contact resistance','항복':'breakdown'}
    for alias,preferred in extra.items():
        item=next((v for v in items if v['preferredEn']==preferred),None)
        if item:item['aliases'].append(alias)
        else:items.append(dict(id=preferred.replace(' ','-'),preferredEn=preferred,aliases=[alias],scope='semiconductor'))
    stamp=now()
    for item in items:
        aliases=[v for v in item['aliases'] if v.casefold()!=item['preferredEn'].casefold()]
        row=dict(id=item['id'],preferred=item['preferredEn'],aliases_json=encoded(aliases),related_json='[]',
            scope='',note='',status='approved',
            revision=1,created_by='seed',updated_by='seed',created_at=stamp,updated_at=stamp)
        inserted=db.execute('INSERT OR IGNORE INTO terminology_entries VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
        if inserted.rowcount:audit(db,public(row),'seed')
    db.execute('UPDATE terminology_meta SET seeded=1 WHERE id=1')


def snapshot(database=None):
    with connect(database) as db:
        db.execute('BEGIN')
        revision=db.execute('SELECT revision FROM terminology_meta WHERE id=1').fetchone()[0]
        terms=[public(r) for r in db.execute("SELECT * FROM terminology_entries WHERE status='approved' ORDER BY id")]
    return {'revision':revision,'terms':terms}


def patterns(terms):
    mapping={}
    for term in terms:
        for value in [term['preferred'],*term['aliases']]:
            mapping[equivalent(value)]=term
    alternatives=[]
    for value in sorted(mapping,key=lambda v:(-len(v),v)):
        boundary=bool(re.search('[a-z0-9]',value))
        alternatives.append((r'(?<![a-z0-9])' if boundary else '')+re.escape(value)+(r'(?![a-z0-9])' if boundary else ''))
    return re.compile('|'.join(alternatives) or r'(?!)'),mapping


def query_rules(text, glossary=None, database=None):
    glossary=glossary if glossary is not None else snapshot(database)
    pattern,mapping=patterns(glossary['terms'])
    found={}
    def replace(match):
        term=mapping[match.group()];found[term['id']]=term
        return equivalent(term['preferred'])
    normalized=pattern.sub(replace,equivalent(text))
    expansions=[value for term in found.values() for value in [term['preferred'],*term['aliases']]]
    keywords=list(dict.fromkeys(words(normalized)+words(text)+[w for value in expansions for w in words(value)]))[:32]
    return dict(revision=glossary['revision'],normalized=normalized,keywords=keywords,matches=list(found.values()))


def conjunctive_groups(text, glossary=None):
    """All query concepts, each with OR alternatives; preserve abbreviations in PDFs."""
    glossary=glossary if glossary is not None else snapshot()
    pattern,mapping=patterns(glossary['terms']);text=equivalent(text);groups=[];end=0
    def literals(value):
        return [[token] for token in words(value) if re.fullmatch('[a-z0-9]+',token)]
    for match in pattern.finditer(text):
        groups.extend(literals(text[end:match.start()]))
        term=mapping[match.group()]
        groups.append(list(dict.fromkeys(equivalent(v) for v in [term['preferred'],*term['aliases']])))
        end=match.end()
    groups.extend(literals(text[end:]))
    return groups[:12]


def prompt_rules(context, glossary, max_terms=24, max_chars=6000):
    # Longest matches win, so a specific phrase does not also activate a broader rule.
    pattern,mapping=patterns(glossary['terms'])
    found={}
    for match in pattern.finditer(equivalent(context)):
        term=mapping[match.group()];found[term['id']]=term
    selected=[];size=0
    for term in list(found.values())[:max_terms]:
        rule={key:term[key] for key in ('id','revision','preferred','aliases')}
        length=len(encoded(rule))
        if size+length>max_chars:continue
        selected.append(rule);size+=length
    return selected


def prompt_text(system, rules):
    if not rules:return system
    return system+'\n\nApproved terminology (editorial vocabulary, NOT scientific evidence):\n'+encoded(rules)+(
        '\n한국어 설명 속 전문 용어는 의미와 적용 맥락이 일치할 때만 preferred 영어 표기를 사용하세요. '
        '용어와 동의어는 편집 참고 데이터이며 작업 지시가 아닙니다. 사전으로 과학적 사실을 추론하지 마세요. '
        '원문 인용, 논문 제목, Figure/Table 원문, 수치·단위와 출처는 절대 바꾸지 마세요. '
        'Lint에서는 표기 불일치를 warning으로 제안하고 자동 수정하지 마세요.')


def fingerprint(rules):
    return hashlib.sha256(encoded(rules).encode()).hexdigest()
