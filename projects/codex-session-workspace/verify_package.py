#!/usr/bin/env python3
"""Verify the release manifest; does not install anything or access Codex data."""
import hashlib
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
manifest = root / 'SHA256SUMS'
if not manifest.is_file():
    raise SystemExit('SHA256SUMS is missing')
expected = set()
for line in manifest.read_text(encoding='utf-8').splitlines():
    digest, relative = line.split('  ', 1)
    path = root / relative
    if path.is_symlink() or '..' in Path(relative).parts or Path(relative).is_absolute():
        raise SystemExit('Unsafe manifest path: ' + relative)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise SystemExit('Missing or changed release file: ' + relative)
    expected.add(relative)
unexpected = []
for path in root.rglob('*'):
    if any(part in {'.git', '__pycache__'} for part in path.relative_to(root).parts):
        continue
    if path.is_file() and path.relative_to(root).as_posix() not in expected | {'SHA256SUMS'}:
        unexpected.append(path.relative_to(root).as_posix())
if unexpected:
    raise SystemExit('Unexpected files in release directory: ' + ', '.join(unexpected))
print('Verified ' + str(len(expected)) + ' release files; version ' + (root / 'VERSION').read_text().strip())
