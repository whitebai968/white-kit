#!/usr/bin/env python3
"""Preview or apply ONLY the session-workspace rules, preserving other instructions."""
import argparse
from datetime import datetime
import difflib
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile

START = '<!-- codex-session-workspace:rules:start -->'
END = '<!-- codex-session-workspace:rules:end -->'


def merge_rules(original, fragment, replace_file_rules=False):
    block = START + '\n\n' + fragment.strip() + '\n\n' + END
    if START in original or END in original:
        if original.count(START) != 1 or original.count(END) != 1 or original.index(END) < original.index(START):
            raise ValueError('Incomplete or ambiguous managed-rule markers; merge manually')
        a, b = original.index(START), original.index(END) + len(END)
        return original[:a] + block + original[b:]
    matches = list(re.finditer(r'^# 文件与产物管理\s*$', original, re.M))
    if matches:
        if not replace_file_rules or len(matches) != 1:
            raise ValueError('Existing file-management rules found; inspect the diff and use --replace-file-rules, or merge manually')
        start = matches[0].start()
        following = re.search(r'^# (?!#)', original[matches[0].end():], re.M)
        stop = matches[0].end() + following.start() if following else len(original)
        return original[:start] + block + '\n\n' + original[stop:]
    return (original.rstrip() + '\n\n' if original.strip() else '') + block + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser())
    p.add_argument('--target', choices=['auto', 'AGENTS.md', 'AGENTS.override.md'], default='auto')
    p.add_argument('--replace-file-rules', action='store_true')
    p.add_argument('--apply', action='store_true', help='Without this option, print a diff only')
    p.add_argument('--backup-dir', type=Path)
    args = p.parse_args()
    home = args.codex_home.absolute()
    for parent in [home, *home.parents]:
        if parent.is_symlink():
            raise SystemExit('Refusing a symlink profile directory; inspect and choose its real path explicitly')
    override = home / 'AGENTS.override.md'
    name = args.target
    if name == 'auto':
        name = 'AGENTS.override.md' if override.is_file() and override.stat().st_size else 'AGENTS.md'
    target = home / name
    if target.is_symlink():
        raise SystemExit('Refusing a symlink instruction file')
    original_bytes = target.read_bytes() if target.exists() else None
    original = original_bytes.decode('utf-8') if original_bytes is not None else ''
    fragment = (Path(__file__).resolve().parent / 'rules/AGENTS.fragment.md').read_text(encoding='utf-8')
    try:
        proposed = merge_rules(original, fragment, args.replace_file_rules)
    except ValueError as error:
        raise SystemExit(str(error))
    if proposed == original:
        print('Already up to date: ' + str(target))
        return
    if not args.apply:
        print(''.join(difflib.unified_diff(original.splitlines(True), proposed.splitlines(True), fromfile=str(target), tofile=str(target) + ' (proposed)')))
        return
    if original_bytes is not None and not args.backup_dir:
        raise SystemExit('--backup-dir is required before replacing an existing instruction file')
    if (target.read_bytes() if target.exists() else None) != original_bytes:
        raise SystemExit('Instructions changed while preparing the update; retry after reviewing')
    if original_bytes is not None:
        backup = args.backup_dir / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + hashlib.sha256(original_bytes).hexdigest()[:8])
        backup.mkdir(parents=True, exist_ok=False)
        shutil.copy2(target, backup / target.name)
        print('Backup: ' + str(backup / target.name))
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix='.session-rules-', dir=home)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(proposed)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, target.stat().st_mode & 0o777 if target.exists() else 0o600)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    print('Applied: ' + str(target))


if __name__ == '__main__':
    main()
