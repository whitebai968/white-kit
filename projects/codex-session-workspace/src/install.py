#!/usr/bin/env python3
"""Install/control the per-user macOS LaunchAgent. Does not change Codex settings."""
import argparse
import datetime
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

LABEL = 'local.codex.session-mirror'


def bootstrap(domain, plist):
    # launchd can briefly reject a reload while the old job is exiting.
    result = None
    for delay in (0, 0.5, 1, 2, 4):
        if delay:
            time.sleep(delay)
        result = subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], capture_output=True, text=True)
        if result.returncode == 0:
            return
    raise RuntimeError('LaunchAgent could not start: ' + (result.stderr or result.stdout).strip())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['install', 'status', 'stop', 'start'])
    p.add_argument('--seed', action='append', default=[])
    p.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser())
    p.add_argument('--backup-dir', type=Path)
    args = p.parse_args()
    root = Path.home() / 'Library/Application Support/CodexSessionMirror'
    state = root / 'state'
    plist = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    domain = 'gui/' + str(os.getuid())
    service = domain + '/' + LABEL
    if args.action == 'status':
        result = subprocess.run(['launchctl', 'print', service], capture_output=True, text=True)
        print(result.stdout or result.stderr)
        status = state / 'state.json'
        if status.exists():
            data = json.loads(status.read_text())
            print(json.dumps({'last_scan_at': data.get('last_scan_at'), 'scan_error': data.get('scan_error'),
                              'sessions': data.get('sessions')}, ensure_ascii=False, indent=2))
        return
    if args.action == 'stop':
        subprocess.run(['launchctl', 'bootout', service], check=True)
        print('Stopped. Existing copies and state retained. Start with: python3 install.py start')
        return
    if args.action == 'start':
        bootstrap(domain, plist)
        print('Started ' + LABEL)
        return
    if sys.platform != 'darwin':
        raise SystemExit('This installer requires macOS; worker tests are platform independent on Unix.')
    if sys.version_info < (3, 9):
        raise SystemExit('Python 3.9 or later is required')
    from session_mirror import read_inventory
    inventory, _, _ = read_inventory(args.codex_home.resolve())
    missing_seeds = [sid for sid in args.seed if sid not in inventory]
    if missing_seeds:
        raise SystemExit('Seed session IDs are not present in this local Codex profile: ' + ', '.join(missing_seeds))
    owner = root / 'installation.json'
    if root.exists() and not owner.exists():
        raise SystemExit('Installation directory exists without an ownership marker; refusing overwrite')
    if owner.exists() and json.loads(owner.read_text()).get('owner') != LABEL:
        raise SystemExit('Installation ownership mismatch')
    if plist.exists():
        with plist.open('rb') as f:
            if plistlib.load(f).get('Label') != LABEL:
                raise SystemExit('LaunchAgent ownership mismatch')
    source = Path(__file__).resolve().parent
    worker = source / 'session_mirror.py'
    if not worker.is_file():
        raise SystemExit('Missing session_mirror.py alongside installer')
    if owner.exists():
        if not args.backup_dir:
            raise SystemExit('Use --backup-dir to keep the prior installed version before updating')
        backup = args.backup_dir / datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        backup.mkdir(parents=True)
        for file in [root / 'session_mirror.py', root / 'task_layout.py', root / 'session_layout.py', root / 'archive_store.py', root / 'install.py', root / 'README.md', owner, plist]:
            if file.exists():
                shutil.copy2(file, backup / file.name)
    os.umask(0o077)
    state.mkdir(parents=True, exist_ok=True)
    plist.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['launchctl', 'bootout', service], capture_output=True)
    for name in ['session_mirror.py', 'session_layout.py', 'archive_store.py', 'install.py', 'README.md']:
        src, dst = source / name, root / name
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
    # Obsolete task-layout module is backed up above and is not used by v3.
    (root / 'task_layout.py').unlink(missing_ok=True)
    owner.write_text(json.dumps({'owner': LABEL, 'source_directory': str(source)}, indent=2) + '\n')
    argv = [sys.executable, str(root / 'session_mirror.py'), '--state-dir', str(state),
            '--codex-home', str(args.codex_home.resolve()), '--interval', '2', '--delete-grace', '15']
    for sid in args.seed:
        argv += ['--seed', sid]
    job = {'Label': LABEL, 'ProgramArguments': argv, 'RunAtLoad': True, 'KeepAlive': True,
           'ThrottleInterval': 10, 'ProcessType': 'Background', 'WorkingDirectory': str(root),
           'Umask': 0o077, 'StandardOutPath': str(state / 'launchd.stdout.log'),
           'StandardErrorPath': str(state / 'launchd.stderr.log')}
    with plist.open('wb') as f:
        plistlib.dump(job, f)
    subprocess.run(['plutil', '-lint', str(plist)], check=True)
    bootstrap(domain, plist)
    print(json.dumps({'installed': True, 'label': LABEL, 'runtime': str(root), 'plist': str(plist),
                      'interval_seconds': 2, 'delete_grace_seconds': 15}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
