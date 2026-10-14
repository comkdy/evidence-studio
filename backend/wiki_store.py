"""Versioned Markdown store. SQLite is a rebuildable catalogue, never the body source."""
from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4
from functools import lru_cache
from .pilot_db import write_connection, ROOT, DEFAULT_DB, connect, db_path, now


class WikiError(ValueError):
    pass


def digest(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def wiki_root(database=None):
    database = Path(database or db_path()).resolve()
    return ROOT / 'wiki' if database == DEFAULT_DB.resolve() else database.parent / 'wiki'


def safe_path(root, relative):
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or candidate == root.resolve():
        raise WikiError('Wiki 경로가 저장소 밖을 가리킵니다.')
    return candidate


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temp.write_text(content, encoding='utf-8', newline='\n')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_if_changed(path, content):
    if not path.exists() or path.read_text(encoding='utf-8') != content:
        atomic_write(path, content)


def parse_markdown(text):
    try:
        front, body = text.split('\n---\n', 1)
        if not front.startswith('---\n'):
            raise ValueError()
        meta = json.loads(front[4:])  # JSON is a YAML subset; no unsafe YAML loader.
        if not isinstance(meta, dict):
            raise ValueError()
        return meta, body.lstrip('\n')
    except (ValueError, TypeError):
        raise WikiError('Wiki frontmatter가 올바르지 않습니다.') from None


@lru_cache(maxsize=256)
def verified_markdown(path,mtime,size,expected):
    text=Path(path).read_text(encoding='utf-8')
    if digest(text)!=expected:
        raise WikiError('외부에서 변경한 Wiki입니다. lint 및 adopt 후 검색에 반영하세요.')
    return parse_markdown(text)


class WikiStore:
    def __init__(self, database=None):
        self.database = Path(database or db_path()).resolve()
        self.root = wiki_root(self.database)

    def rows(self):
        with connect(self.database) as db:
            return [dict(r) for r in db.execute('SELECT * FROM wiki_files ORDER BY path')]

    def record(self, page_id):
        with connect(self.database) as db:
            row = db.execute('SELECT * FROM wiki_files WHERE id=?', (page_id,)).fetchone()
            return dict(row) if row else None

    def read(self, row):
        try:
            path=safe_path(self.root,row['path']); stat=path.stat()
            meta,body=verified_markdown(str(path),stat.st_mtime_ns,stat.st_size,row['content_hash'])
        except OSError:
            raise WikiError('Wiki 파일이 없습니다. lint 결과를 확인하세요.') from None
        if meta.get('id') != row['id'] or meta.get('revision') != row['revision']:
            raise WikiError('Wiki 버전 정보가 일치하지 않습니다.')
        return meta, body

    def publish(self, page_id, relative, title, body, refs, input_hash, *,
                scope='common', expert_id=None, kind='topic', status='ready', producer='evidence-reuse', reason='knowledge update', adopt=False):
        old = self.record(page_id)
        if old:
            if not adopt:
                self.read(old)  # Never overwrite unadopted human edits.
            if old['input_hash'] == input_hash and old['status'] != 'archived' and not adopt:
                return old
        revision = old['revision'] + 1 if old else 1
        stamp = now()
        meta = dict(id=page_id, title=title, scope=scope, expert_id=expert_id, kind=kind,
                    revision=revision, updated_at=stamp, status=status, producer=producer,
                    input_hash=input_hash, refs=refs)
        text = '---\n' + json.dumps(meta, ensure_ascii=False, indent=2) + '\n---\n\n' + body.rstrip() + '\n'
        content_hash = digest(text)
        history = f'.history/{digest(page_id)[:24]}/{revision:06d}-{content_hash[:12]}.md'
        path = safe_path(self.root, relative)
        previous = path.read_text(encoding='utf-8') if path.exists() else None
        if old and not adopt and digest(previous or '')!=old['content_hash']:
            raise WikiError('Wiki 파일이 편집되었습니다. 변경을 확인한 뒤 다시 실행하세요.')
        # A history write is immutable. The mutable file is replaced atomically; on DB
        # failure we restore it. On process crash, hash checks fail closed for repair.
        atomic_write(safe_path(self.root, history), text)
        replaced=False
        try:
            with write_connection(self.database) as db:
                current = db.execute('SELECT content_hash FROM wiki_files WHERE id=?', (page_id,)).fetchone()
                if (current['content_hash'] if current else None) != (old['content_hash'] if old else None):
                    raise WikiError('다른 작업이 Wiki를 갱신했습니다. 다시 시도하세요.')
                if (path.read_text(encoding='utf-8') if path.exists() else None)!=previous:
                    raise WikiError('Wiki 파일이 편집되었습니다. 변경을 확인한 뒤 다시 실행하세요.')
                atomic_write(path, text)
                replaced=True
                db.execute('INSERT OR REPLACE INTO wiki_files VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (page_id, relative, scope, expert_id, kind, title, input_hash, content_hash,
                            revision, json.dumps(refs, ensure_ascii=False), status, producer, stamp))
                db.execute('INSERT INTO wiki_revisions VALUES(?,?,?,?,?,?,?,?,?)',
                           (page_id, revision, history, content_hash, input_hash, json.dumps(refs, ensure_ascii=False), producer, reason, stamp))
                db.execute('DELETE FROM wiki_fts WHERE page_id=?', (page_id,))
                db.execute('INSERT INTO wiki_fts VALUES(?,?,?)', (page_id, title, body))
                db.commit()
        except Exception:
            if replaced and previous is not None:
                atomic_write(path, previous)
            elif replaced and path.exists():
                path.unlink()
            raise
        return self.record(page_id)

    def index_body(self, active_rows):
        """A deterministic catalogue, not another model-authored knowledge page."""
        rows = sorted((r for r in active_rows if r['status'] != 'archived'), key=lambda r: (r['title'], r['path']))
        groups = [
            ('전문가', [r for r in rows if r['kind'] == 'expert_index']),
            ('공통 주제', [r for r in rows if r['scope'] == 'common' and r['kind'] == 'topic']),
            ('논문별 원천 지식', [r for r in rows if r['scope'] == 'common' and r['kind'] == 'source']),
        ]
        lines = ['# Evidence Studio Wiki', '',
                 '문서의 위치와 상태를 안내하는 자동 생성 목차입니다. 과학적 사실은 연결된 Wiki와 원문 근거에서 확인하세요.', '',
                 '논문별 원천 지식 → 공통 주제 종합 → 전문가별 지식으로 연결됩니다. 종합 대기와 작성 완료는 구분합니다.', '']
        for heading, entries in groups:
            lines += ['## '+heading+f' ({len(entries)})', '']
            for r in entries:
                refs = json.loads(r['refs_json'])
                label = re.sub(r'([\\`*{}\[\]<>#])', r'\\\1', r['title']).replace('\n', ' ')
                status = {'awaiting_llm': '종합 작성 대기', 'needs_source': '원문 확인 필요', 'empty': '자료 없음'}.get(r['status'])
                if status is None:
                    status = {'source': '원천 정리', 'expert_index': '지식 연결'}.get(r['kind'], '종합 작성')
                lines += [f'- [{label}]({r["path"]}) — {status} · 근거 논문 {len(refs["paper_ids"])}편 · v{r["revision"]} · {r["updated_at"][:10]}']
            if not entries:
                lines += ['아직 연결된 문서가 없습니다.']
            lines += ['']
        lines += ['전문가 특화 노트는 해당 전문가의 index에서 확인할 수 있습니다.', '',
                  '[공통 작성 지침](AGENTS.md) · [변경 이력](log.md) · [최근 점검](lint.md)', '']
        return '\n'.join(lines)

    def index_problems(self, active_rows):
        try:
            return (self.root/'index.md').read_text(encoding='utf-8') != self.index_body(active_rows)
        except (OSError, UnicodeError):
            return True

    def navigation(self, active_rows):
        rules = self.root / 'AGENTS.md'
        if not rules.exists():
            from .wiki_policy import POLICY_PATH
            policy_link = os.path.relpath(POLICY_PATH, self.root).replace('\\', '/')
            atomic_write(rules, '# Evidence Studio Wiki 작업 안내\n\n'
                         f'작업 전에 [공통 작성 지침](<{policy_link}>)을 읽고 따르세요.\n\n'
                         '작성 규칙의 원본은 위 파일 하나입니다. 이 파일에 규칙을 복사하지 마세요.\n'
                         'ingest·답변·lint도 같은 원본을 읽습니다. index.md와 log.md는 코드가 관리합니다.\n')
        write_if_changed(self.root / 'index.md', self.index_body(active_rows))
        # Re-materialize the complete append-only DB revision ledger; no truncation.
        with connect(self.database) as db:
            changes = list(db.execute('SELECT r.*,f.title FROM wiki_revisions r JOIN wiki_files f ON f.id=r.page_id ORDER BY r.created_at,r.page_id,r.revision'))
        log = '# Wiki 변경 이력\n\n' + '\n'.join(
            f'- {r["created_at"]} · [{r["title"]} · v{r["revision"]}]({r["path"]}) · {r["reason"]}' for r in changes)
        write_if_changed(self.root / 'log.md', log + '\n')
        if not (self.root / 'lint.md').exists():
            atomic_write(self.root / 'lint.md', '# Wiki 점검\n\n아직 정기 점검을 실행하지 않았습니다.\n')
