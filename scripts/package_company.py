"""Build an explicit, clean company handoff ZIP without secrets or generated data."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('backend', 'src', 'shared', 'tests', 'scripts', 'docs', 'dist', 'raw')
ROOT_FILES = ('.env.example', '.gitignore', 'README.md', 'index.html', 'package.json',
              'pnpm-lock.yaml', 'pnpm-workspace.yaml', 'tsconfig.json', 'vite.config.ts')
EXCLUDED_DOCS = {'CORPUS_REVIEW_2026-10-13.md'}
IGNORED_PARTS = {'__pycache__', 'node_modules', '.venv', '.git', '.pytest_cache'}
FORBIDDEN_SUFFIXES = {'.pyc', '.pyo', '.sqlite', '.sqlite3', '.db', '.log', '.zip'}
PREFIX = 'Evidence-Studio/'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def build(output):
    output = Path(output).resolve()
    if output.exists():
        raise SystemExit('Output already exists; choose a new output name.')
    # Backups can remain locally; the allowlist never includes them.
    if any((ROOT / 'data').glob('evidence-studio.sqlite3*')):
        raise SystemExit('Stop the server and explicitly reset the active database first.')
    unexpected = [p for p in (ROOT / 'wiki').rglob('*') if p.is_file() and p.name != '.gitkeep']
    if unexpected:
        raise SystemExit('The active wiki is not empty. Reset it before packaging.')
    if not (ROOT / 'dist/index.html').is_file():
        raise SystemExit('Run pnpm build before packaging.')
    template = (ROOT / '.env.example').read_text(encoding='utf-8-sig')
    for line in template.splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            if (key.endswith('_API_KEY') or key.endswith('_BASE_URL') or key.endswith('_MODEL')) and value.strip():
                raise SystemExit('The environment template must not contain credentials or service values.')
    paths = [ROOT / name for name in ROOT_FILES]
    for directory in DIRECTORIES:
        for path in sorted((ROOT / directory).rglob('*')):
            if path.is_symlink():
                raise SystemExit('Symlinks are not supported in the handoff bundle.')
            if not path.is_file() or any(part in IGNORED_PARTS for part in path.relative_to(ROOT).parts):
                continue
            if path.suffix.lower() in FORBIDDEN_SUFFIXES:
                continue
            if directory == 'docs' and path.name in EXCLUDED_DOCS:
                continue
            if directory == 'raw' and path.suffix.lower() != '.pdf' and path.name != '.gitkeep':
                continue
            paths.append(path)
    paths.extend(ROOT / name / '.gitkeep' for name in ('data', 'wiki'))
    paths = sorted(set(paths))
    manifest = {
        'app': 'Evidence Studio',
        'release': 'company-handoff-2026-10-14',
        'database': 'Absent; schema and baseline settings initialize on first launch.',
        'wiki': 'Empty; documents are regenerated after ingest.',
        'raw_pdf_count': sum(p.suffix.lower() == '.pdf' and p.parent == ROOT / 'raw' for p in paths),
        'excluded': ['.env', '.git', '.venv', 'node_modules', 'artifacts', 'tmp',
                     'generated databases, wiki bodies/history, curation JSON and backups',
                     'docs/CORPUS_REVIEW_2026-10-13.md'],
        'files': []
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in paths:
            if not path.resolve().is_relative_to(ROOT.resolve()):
                raise SystemExit('Source path escapes project.')
            relative = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            archive.writestr(PREFIX + relative, data)
            manifest['files'].append({'path': relative, 'bytes': len(data), 'sha256': sha(data)})
        archive.writestr(PREFIX + 'PACKAGE_MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None, 'Archive CRC validation failed'
        for record in manifest['files']:
            assert sha(archive.read(PREFIX + record['path'])) == record['sha256'], record['path']
        assert [n for n in archive.namelist() if n.startswith(PREFIX + 'data/')] == [PREFIX + 'data/.gitkeep']
        assert [n for n in archive.namelist() if n.startswith(PREFIX + 'wiki/')] == [PREFIX + 'wiki/.gitkeep']
        assert PREFIX + '.env' not in archive.namelist()
    archive_hash = sha(output.read_bytes())
    output.with_suffix('.zip.sha256').write_text(archive_hash + '  ' + output.name + '\n', encoding='utf-8')
    print(json.dumps({'archive': str(output), 'bytes': output.stat().st_size,
                      'files': len(manifest['files']), 'raw_pdf_count': manifest['raw_pdf_count'],
                      'sha256': archive_hash}, ensure_ascii=False))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output)
