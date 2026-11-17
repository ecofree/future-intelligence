"""Compare a wheel and its installed files with the tracked source snapshot.

No package publication, model calls, database writes or acceptance promotion.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
from importlib import import_module, metadata
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tomllib
import zipfile

PROTOCOL = 'fi.distribution-snapshot.v1'
PACKAGE = 'future_intelligence'


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def safe_name(name: str) -> str:
    path = PurePosixPath(name)
    if not name or '\\' in name or path.is_absolute() or '..' in path.parts or str(path) != name:
        raise ValueError('unsafe archive path')
    return name


def source_snapshot(root: Path) -> dict:
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], timeout=15)
    paths = git('ls-files', '-z', '--', PACKAGE).decode().split('\x00')
    files = {}
    for name in filter(None, paths):
        safe_name(name)
        path = root / name
        if path.is_symlink() or not path.is_file() or path.suffix not in ('.py', '.sql'):
            raise ValueError('unexpected tracked package resource')
        files[name] = digest(path.read_bytes())
    actual = {p.relative_to(root).as_posix() for p in (root / PACKAGE).rglob('*')
              if p.is_file() and '__pycache__' not in p.parts}
    if not files or actual != set(files):
        raise ValueError('package contains untracked or missing resources')
    version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    return {'protocol': PROTOCOL, 'version': version,
            'commit': git('rev-parse', 'HEAD').decode().strip(),
            'tree': git('rev-parse', 'HEAD^{tree}').decode().strip(),
            'files': dict(sorted(files.items()))}


def verify_wheel(snapshot: dict, wheel: Path) -> dict:
    if snapshot.get('protocol') != PROTOCOL:
        raise ValueError('unknown snapshot protocol')
    prefix = PACKAGE + '-' + snapshot['version'] + '.dist-info/'
    allowed_metadata = {prefix + x for x in ('METADATA', 'WHEEL', 'RECORD', 'top_level.txt')}
    files, seen, meta = {}, set(), None
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            name = safe_name(member.filename.rstrip('/') if member.is_dir() else member.filename)
            if name in seen:
                raise ValueError('duplicate archive path')
            seen.add(name)
            if stat.S_IFMT(member.external_attr >> 16) == stat.S_IFLNK:
                raise ValueError('archive symlink rejected')
            if member.is_dir():
                continue
            if member.file_size > 10_000_000:
                raise ValueError('oversized package resource')
            raw = archive.read(member)
            if name.startswith(PACKAGE + '/'):
                files[name] = digest(raw)
            elif name not in allowed_metadata:
                raise ValueError('unexpected wheel content')
            if name == prefix + 'METADATA':
                meta = BytesParser().parsebytes(raw)
    if files != snapshot['files']:
        raise ValueError('wheel differs from tracked source')
    if meta is None or meta['Name'] != 'future-intelligence' or meta['Version'] != snapshot['version']:
        raise ValueError('wheel metadata version mismatch')
    return {'status': 'WHEEL_MATCHES_SOURCE', 'version': snapshot['version'],
            'package_files': len(files), 'wheel_sha256': digest(wheel.read_bytes()),
            'production_acceptance': False}


def verify_installed(snapshot: dict) -> dict:
    if snapshot.get('protocol') != PROTOCOL:
        raise ValueError('unknown snapshot protocol')
    package = import_module(PACKAGE)
    if metadata.version('future-intelligence') != snapshot['version'] or package.__version__ != snapshot['version']:
        raise ValueError('installed version mismatch')
    root = Path(package.__file__).resolve().parent
    files = {PACKAGE + '/' + p.relative_to(root).as_posix(): digest(p.read_bytes())
             for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if files != snapshot['files']:
        raise ValueError('installed package differs from source snapshot')
    return {'status': 'INSTALLED_FILES_MATCH_SOURCE', 'version': snapshot['version'],
            'package_files': len(files),
            'migration_files': sum(name.endswith('.sql') for name in files),
            'production_acceptance': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['snapshot', 'wheel', 'installed'])
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('wheel', type=Path, nargs='?')
    args = parser.parse_args()
    if args.mode == 'snapshot':
        result = source_snapshot(Path(__file__).resolve().parents[1])
        with args.snapshot.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, sort_keys=True, indent=2)
        print(json.dumps({'status': 'SOURCE_SNAPSHOT_WRITTEN', 'commit': result['commit'], 'tree': result['tree']}))
        return
    snapshot = json.loads(args.snapshot.read_text(encoding='utf-8'))
    if args.mode == 'wheel' and args.wheel is None:
        parser.error('wheel path required')
    result = verify_wheel(snapshot, args.wheel) if args.mode == 'wheel' else verify_installed(snapshot)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
