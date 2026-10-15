"""Standalone, script-free HTML; model output is always escaped text."""
from html import escape
from .answer_validation import KINDS


def source_details(ev):
    if ev['kind'] == 'claim':
        claim = ev['claim']
        return claim.get('sources', []), claim.get('summary', '')
    return [dict(quote=ev['text'], page_number=ev['page_number'], document_id=ev['document_id'])], ''


def render_html(report):
    document = report['document']
    e = lambda value: escape(str(value), quote=True)
    sections, references, indices = [], [], {}
    for section in document['sections']:
        rows = []
        for st in section['statements']:
            links = []
            for key in st['evidence_ids']:
                if key not in indices:
                    indices[key] = len(indices)+1
                    ev = section['sources'][key]
                    spans, summary = source_details(ev)
                    quotes = ''.join('<blockquote>'+e(s['quote'])+'</blockquote><small>p. '+e(s.get('page_number', '?'))+'</small>' for s in spans)
                    references.append('<li id="ref-'+str(indices[key])+'"><strong>'+e(ev['paper_title'])+'</strong><p>'+e(summary)+'</p>'+quotes+'</li>')
                links.append('<a href="#ref-'+str(indices[key])+'">['+str(indices[key])+']</a>')
            text = e(st['text'])+' <span class="citations">'+''.join(links)+'</span>'
            kind = e(KINDS[st['kind']])
            rows.append('<tr><td>'+kind+'</td><td>'+text+'</td></tr>' if section['layout']=='table'
                        else '<li><small>'+kind+'</small><p>'+text+'</p></li>')
        content = ('<table><thead><tr><th>구분</th><th>비교 내용 · 조건 · 근거</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table>'
                   if section['layout']=='table' else '<ul class="'+section['layout']+'">'+''.join(rows)+'</ul>')
        sections.append('<section><h2>'+e(section['title'])+'</h2>'+ (content if rows else '<p class="empty">작성 전</p>')+'</section>')
    return '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+e(document['title'])+'</title><style>'+STYLE+'</style><body><main><header><small>EVIDENCE STUDIO · v'+str(report['revision'])+'</small><h1>'+e(document['title'])+'</h1><p>'+e(document['purpose'])+'</p><small>'+e(report['updated_at'])+'</small></header>'+''.join(sections)+'<section><h2>인용 근거</h2><p>보고서 작성 당시의 근거 사본입니다. 종합 해석·가설·제안은 논문에서 직접 확인된 사실과 구분해 검토하세요.</p><ol class="references">'+''.join(references)+'</ol></section></main></body></html>'


STYLE = '''body{margin:0;background:#f4f5f7;color:#24323e;font-family:system-ui,"Malgun Gothic",sans-serif;line-height:1.8}main{max-width:960px;margin:40px auto;padding:48px;background:white}header{border-bottom:3px solid #376a6f;padding-bottom:24px}h1{font-size:32px;line-height:1.4}h2{margin-top:36px;font-size:22px}small,.empty{color:#60717e}p,blockquote,li,td{overflow-wrap:anywhere;white-space:pre-wrap}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:14px;border:1px solid #dbe1e5}td:first-child{min-width:88px;font-size:12px}a{color:#226970}.citations a{margin-left:5px}.prose{padding:0;list-style:none}.prose li{margin-bottom:22px}li p{margin-top:4px}blockquote{border-left:3px solid #a7c4c6;margin:12px 0;padding:10px 16px;background:#f5f8f8}.references>li{margin-bottom:28px}@media(max-width:650px){main{margin:0;padding:22px}h1{font-size:25px}td,th{padding:8px}}@media print{body{background:white}main{margin:0;padding:0}h2{break-after:avoid}tr,blockquote{break-inside:avoid}}'''
