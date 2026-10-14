"""Offline, escaped HTML/JSON diagnostic reports. Never embeds provider responses."""
import html
import json
from pathlib import Path


def write_report(directory, report):
    directory = Path(directory)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    target = directory / 'report.json'
    temporary = directory / 'report.json.tmp'
    temporary.write_text(encoded, encoding='utf-8')
    temporary.replace(target)
    esc = lambda v: html.escape(str(v), quote=True)
    labels = {'passed': '성공', 'failed': '실패', 'skipped': '미실행', 'running': '실행 중'}
    rows = []
    for result in report['results']:
        checks = ''.join('<li class="'+esc(c['status'])+'"><b>'+labels.get(c['status'], esc(c['status']))+
                         '</b> '+esc(c['name'])+'<small>'+esc(c.get('detail', ''))+'</small></li>' for c in result.get('checks', []))
        models = []
        calls = result.get('calls', [])
        for key in dict.fromkeys((c['service'], c['model']) for c in calls):
            own = [c for c in calls if (c['service'], c['model']) == key]
            def tokens(field):
                values = [c.get(field) for c in own if c.get(field) is not None]
                return f'{sum(values):,}' + (' (일부)' if len(values) < len(own) else '') if values else '미제공'
            models.append('<tr><td>'+esc(key[0])+'<small>'+esc(key[1])+'</small></td><td>'+str(len(own))+
                          '</td><td>'+tokens('input_tokens')+'</td><td>'+tokens('output_tokens')+'</td><td>'+tokens('total_tokens')+
                          '</td><td>'+f"{sum(c['elapsed_ms'] for c in own)/1000:,.1f}초"+'</td></tr>')
        usage = ('<div class="table-wrap"><table><thead><tr><th>서비스 / 모델</th><th>HTTP 시도</th><th>입력 토큰</th><th>출력 토큰</th><th>전체 토큰</th><th>응답 시간</th></tr></thead><tbody>'+
                 ''.join(models)+'</tbody></table></div>') if models else '<p class="muted">이 단계에서 기록된 실제 API 호출 없음</p>'
        failed_calls = [c for c in calls if c['status'] != 'completed']
        if failed_calls:
            usage += '<p class="muted">미완료 호출: '+esc(', '.join(f"{c['service']} {c['status']}"+(f" · HTTP {c['http_status']}" if c.get('http_status') else '') for c in failed_calls))+'</p>'
        rows.append('<section><header><div><span class="eyebrow">'+esc(result['step'])+'</span><h2>'+esc(result['title'])+'</h2></div><span class="badge '+esc(result['status'])+'">'+labels[result['status']]+'</span></header><p>'+esc(result.get('summary', ''))+'</p><p class="muted">'+f"{result.get('elapsed_ms', 0)/1000:,.1f}초"+' · '+esc(result.get('mode', 'local'))+'</p><ul>'+checks+'</ul>'+usage+'</section>')
    counts = {s: sum(r['status'] == s for r in report['results']) for s in labels}
    document = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:"><title>Evidence Studio · 단계별 점검 보고서</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f2f5fa;color:#24354d;font:15px/1.65 system-ui,"Malgun Gothic",sans-serif}main{max-width:1050px;margin:auto;padding:48px 24px}h1{font-size:30px;margin:5px 0 12px}h2{font-size:18px;margin:4px 0}p{margin:8px 0}.eyebrow{font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#6482a7}.muted,small{color:#63758b;font-size:12px}small{display:block;overflow-wrap:anywhere}.intro{max-width:820px}.totals{display:flex;gap:12px;flex-wrap:wrap;margin:26px 0}.totals div{background:white;padding:12px 24px;border:1px solid #dce4ee;border-radius:9px}.totals b{font-size:24px;margin-left:12px}section{background:white;border:1px solid #dce4ee;border-radius:12px;padding:24px;margin:18px 0}header{display:flex;justify-content:space-between;gap:20px}.badge{height:fit-content;white-space:nowrap;padding:3px 10px;border-radius:5px;font-size:12px}.passed.badge{background:#e9f5ed;color:#297448}.failed.badge{background:#faeae8;color:#aa4037}.skipped.badge,.running.badge{background:#edf1f7;color:#65758b}ul{list-style:none;padding:0}li{border-top:1px solid #edf0f5;padding:9px 0}li>b{font-size:11px;margin-right:12px}.passed>b{color:#297448}.failed>b{color:#aa4037}.skipped>b{color:#7a8699}table{width:100%;border-collapse:collapse;font-size:12px;text-align:left}th,td{padding:10px 8px;border-bottom:1px solid #e7edf4;min-width:85px}th{color:#65758b;font-weight:500}td:first-child{min-width:150px;max-width:350px;overflow-wrap:anywhere}.table-wrap{overflow-x:auto;margin-top:18px}.note{padding:16px 20px;border-left:3px solid #7796bd;background:#e9eff7}footer{margin-top:28px;font-size:12px;color:#65758b}@media(max-width:600px){main{padding:24px 14px}section{padding:16px}h1{font-size:24px}.totals{gap:7px}.totals div{padding:8px 14px}}@media print{body{background:white}main{max-width:none;padding:0}section{break-inside:avoid}.table-wrap{overflow:visible}}
    </style><main><span class="eyebrow">Evidence Studio / Diagnostics</span><h1>단계별 점검 보고서</h1><p class="muted">'''+esc(report['created_at'])+' · '+esc(report['run_id'])+'</p><p class="intro">선택한 단계의 코드·연동·근거 연결을 점검한 결과입니다. 운영 자료 대신 합성 PDF와 격리된 DB·Wiki를 사용했습니다. 실제 모델 품질 전체를 보증하는 평가 결과는 아닙니다.</p><div class="totals">'+''.join('<div>'+label+'<b>'+str(counts[s])+'</b></div>' for s,label in labels.items())+'</div><p class="note">실제 API 허용: '+('예 · 선택 단계에서 설정된 서비스만 호출' if report['live'] else '아니요 · 실제 연동 단계는 미실행')+'<br>토큰은 API가 반환한 항목만 집계합니다. 미제공·일부 집계·실패를 0으로 대체하지 않습니다. 모델 응답 시간과 단계 전체 시간은 다릅니다.</p>'+''.join(rows)+'<footer>이 보고서에는 API 키·주소·SSO 사번·모델 응답 원문을 기록하지 않습니다. JSON 결과는 같은 폴더의 report.json에 있습니다. SSO·OCR 및 자동 canonical claim 추출은 이 도구의 점검 범위에 포함하지 않습니다.</footer></main></html>'
    temporary = directory / 'report.html.tmp'
    temporary.write_text(document, encoding='utf-8')
    temporary.replace(directory / 'report.html')
