#!/usr/bin/env python3
"""One-way local Codex transcript mirror. Python 3.9+, standard library only."""
import argparse
from datetime import datetime
import fcntl
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import signal
import sqlite3
import tempfile
import time
import unicodedata
import session_layout as layout
import archive_store

MARKER = '.codex-session-mirror.json'
OWNER = 'codex-session-mirror-v1'
ARCHIVE = '已归档'
LOG = logging.getLogger('session-mirror')


def atomic_json(path, value):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.mirror-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def signature(path):
    try:
        s = Path(path).stat()
        return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]
    except FileNotFoundError:
        return None


def safe_name(name, sid):
    name = unicodedata.normalize('NFC', str(name or '未命名会话'))
    name = ''.join('_' if c in '/\\:' or ord(c) < 32 or ord(c) == 127 else c for c in name)
    name = name.strip().strip('.') or '未命名会话'
    while len(name.encode('utf-8')) > 180:
        name = name[:-1]
    if name in {ARCHIVE, 'codex-file', '.codex', '.git', archive_store.META}:
        name += '--' + sid[-8:]
    return name


def ensure_plain_dir(path):
    """Reject symlink traversal for all directories the worker manages."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Expected an absolute non-traversing path')
    for part in [*reversed(path.parents), path]:
        if part.is_symlink():
            raise ValueError('Refusing symlink directory: ' + str(part))
    if not path.is_dir():
        raise FileNotFoundError(str(path))


def marker_at(directory, sid):
    ensure_plain_dir(directory)
    p = Path(directory) / MARKER
    if p.is_symlink():
        raise ValueError('Refusing symlink marker')
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding='utf-8'))
    return data if data.get('owner') == OWNER and data.get('session_id') == sid else None


def read_inventory(home):
    """Read one consistent SQLite snapshot. No writes to Codex's data store."""
    candidates = list(home.glob('state_[0-9]*.sqlite'))
    candidates = [p for p in candidates if re.fullmatch(r'state_\d+\.sqlite', p.name)]
    if not candidates:
        raise RuntimeError('Codex state database unavailable; all mirror mutations suspended')
    db = max(candidates, key=lambda p: int(p.stem.split('_')[-1]))
    dbstat = db.stat()
    identity = [str(db), dbstat.st_dev, dbstat.st_ino]
    c = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=1)
    try:
        c.row_factory = sqlite3.Row
        c.execute('BEGIN')
        cols = {r[1] for r in c.execute('PRAGMA table_info(threads)')}
        required = {'id', 'cwd', 'rollout_path', 'archived', 'title'}
        if not required <= cols:
            raise RuntimeError('Unsupported Codex database schema; no files changed')
        selected = sorted(required | (cols & {'name', 'source', 'thread_source', 'updated_at', 'updated_at_ms'}))
        rows = [dict(r) for r in c.execute('SELECT ' + ','.join(selected) + ' FROM threads')]
    finally:
        c.close()
    index = {}
    p = home / 'session_index.jsonl'
    if p.exists():
        with p.open(encoding='utf-8') as f:
            for line in f:
                try:
                    item = json.loads(line)
                    index[item['id']] = item.get('thread_name')
                except (ValueError, KeyError):
                    continue
    all_ids = {r['id'] for r in rows}
    sessions = {}
    for r in rows:
        if r.get('thread_source') in {'subagent', 'guardian_review'} or 'subagent' in (r.get('source') or ''):
            continue
        if not r.get('cwd') or not r.get('rollout_path'):
            continue
        sid = r['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', sid):
            raise RuntimeError('Unsupported session ID')
        r['display_name'] = r.get('name') or index.get(sid) or r.get('title') or '未命名会话'
        r['fingerprint'] = [r['cwd'], r['rollout_path'], r['archived'], r['display_name'],
                            r.get('updated_at_ms') or r.get('updated_at'), signature(r['rollout_path'])]
        sessions[sid] = r
    return sessions, all_ids, identity


class Mirror:
    file_signature = staticmethod(signature)

    def __init__(self, home, state_dir, seed_ids=(), grace=15):
        self.home = Path(home).resolve()
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_file = self.state_dir / 'state.json'
        if self.state_file.exists():
            self.state = json.loads(self.state_file.read_text(encoding='utf-8'))
            if self.state.get('version') != 1:
                raise RuntimeError('Unsupported mirror state; refusing to reset')
        else:
            self.state = {'version': 1, 'initialized': False, 'baseline': {}, 'sessions': {}}
        self.seeds = set(seed_ids)
        self.grace = grace

    def save(self):
        atomic_json(self.state_file, self.state)

    def validate_source(self, source, sid):
        source = Path(source)
        ensure_plain_dir(source.parent)
        if source.is_symlink() or not source.is_file():
            raise FileNotFoundError(str(source))
        if not any(root in source.parents for root in [self.home / 'sessions', self.home / 'archived_sessions']):
            raise ValueError('Transcript outside Codex session directories')
        with source.open('rb') as f:
            first = json.loads(f.readline())
        meta = first.get('payload', {})
        if first.get('type') != 'session_meta' or (meta.get('id') or meta.get('session_id')) != sid:
            raise ValueError('Transcript identity does not match session')
        return meta

    @staticmethod
    def check_filename(name):
        if name == layout.HISTORY:
            return
        if Path(name).name != name or not name.startswith('rollout-') or not name.endswith('.jsonl'):
            raise ValueError('Invalid managed transcript filename')

    def pick_directory(self, base, name, sid):
        stem = safe_name(name, sid)
        for suffix in ['', '--' + sid[-8:], '--' + sid]:
            dest = base / (stem + suffix)
            if not dest.exists() and not dest.is_symlink():
                return dest
            if not dest.is_symlink() and dest.is_dir() and marker_at(dest, sid):
                return dest
        raise RuntimeError('Workspace names are occupied')

    def recover_move(self, sid, entry):
        move = entry.get('pending_move')
        if not move:
            return
        if move.get('mode') != 'whole_workspace':
            raise RuntimeError('An unfinished move from the previous version needs recovery before upgrade')
        old, dest = Path(move['old']), Path(move['destination'])
        ensure_plain_dir(dest.parent)
        if old.exists():
            marker = marker_at(old, sid)
            if not marker or marker.get('layout_version') != 3:
                raise RuntimeError('Cannot move a workspace without its ownership marker')
            if dest.is_symlink() or (dest.exists() and not dest.samefile(old)):
                raise RuntimeError('Move destination is occupied; no files overwritten')
            os.rename(old, dest)
        elif not dest.exists() or not marker_at(dest, sid):
            raise RuntimeError('Both workspace paths are unavailable; move suspended')
        entry['directory'] = str(dest)
        entry['destination_key'] = move['key']
        entry.pop('pending_move')
        self.save()
        LOG.info('Moved whole session workspace %s: %s -> %s', sid, old, dest)

    def destination(self, record, entry):
        sid = record['id']
        self.recover_move(sid, entry)
        # An archived workspace may have contained the recorded cwd.
        if entry.get('project_directory') and entry.get('recorded_cwd') == record['cwd']:
            project = layout.plain_dir(Path(entry['project_directory']))
        else:
            project = layout.project_root(record['cwd'])
        if project == self.home or self.home in project.parents:
            raise ValueError('Refusing workspace inside Codex state directory')
        base = project
        if record['archived']:
            base = layout.plain_dir(project / ARCHIVE, create=True)
        key = [str(base), safe_name(record['display_name'], sid)]
        old = Path(entry['directory']) if entry.get('directory') else None
        old_marker = marker_at(old, sid) if old and old.exists() else None
        if old and old.exists() and not old_marker:
            raise RuntimeError('Workspace ownership marker missing; refusing to move or overwrite')
        if old_marker and old_marker.get('layout_version') == 3 and entry.get('destination_key') == key:
            dest = old
        else:
            dest = self.pick_directory(base, record['display_name'], sid)
            if old_marker and old_marker.get('layout_version') == 3 and dest != old:
                entry['pending_move'] = {'mode': 'whole_workspace', 'old': str(old), 'destination': str(dest), 'key': key}
                self.save()
                self.recover_move(sid, entry)
            else:
                if not dest.exists():
                    dest.mkdir(mode=0o700)
                    atomic_json(dest / MARKER, {'owner': OWNER, 'session_id': sid, 'layout_version': 3})
                if old_marker and dest != old:
                    entry['legacy_cleanup'] = {'directory': str(old), 'filename': old_marker.get('transcript_file')}
        entry.update({'directory': str(dest), 'destination_key': key, 'project_directory': str(project), 'layout_version': 3, 'id': sid, 'recorded_cwd': record['cwd']})
        for field in ['task_directory', 'previous_task_directory']:
            entry.pop(field, None)
        marker = marker_at(dest, sid)
        if not marker:
            raise RuntimeError('Workspace destination ownership missing')
        # If a v1 mirror was already at project/name, retain its raw file until copy succeeds.
        if marker.get('transcript_file') and marker['transcript_file'] != layout.HISTORY:
            entry['legacy_cleanup'] = {'directory': str(dest), 'filename': marker['transcript_file']}
        marker.update({'owner': OWNER, 'session_id': sid, 'layout_version': 3,
                       'project_directory': str(project), 'transcript_file': layout.HISTORY})
        atomic_json(dest / MARKER, marker)
        self.save()
        layout.initialize(dest)
        return dest, marker

    def copy(self, source, dest, before):
        if dest.is_symlink():
            raise ValueError('Refusing symlink destination')
        fd, temp = tempfile.mkstemp(prefix='.mirror-', dir=dest.parent)
        try:
            digest = hashlib.sha256()
            with source.open('rb') as src, os.fdopen(fd, 'wb') as out:
                left = before[2]
                while left:
                    chunk = src.read(min(left, 1024 * 1024))
                    if not chunk:
                        raise RuntimeError('Source truncated while copying; will retry')
                    out.write(chunk)
                    digest.update(chunk)
                    left -= len(chunk)
                out.flush()
                os.fsync(out.fileno())
            if signature(source) != before:
                raise RuntimeError('Source changed while copying; will retry')
            os.replace(temp, dest)
            return digest.hexdigest()
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def cleanup_legacy(self, sid, entry):
        cleanup = entry.get('legacy_cleanup')
        if not cleanup:
            return
        old = Path(cleanup['directory'])
        if old.exists():
            marker = marker_at(old, sid)
            if not marker:
                raise RuntimeError('Old mirror ownership changed; cleanup suspended')
            filename = cleanup.get('filename')
            if filename:
                self.check_filename(filename)
                file = old / filename
                if file.is_symlink():
                    raise ValueError('Refusing symlink legacy file')
                if file != Path(entry['directory']) / layout.HISTORY:
                    file.unlink(missing_ok=True)
            if old != Path(entry['directory']):
                (old / MARKER).unlink()
                try:
                    old.rmdir()
                except OSError:
                    pass
        entry.pop('legacy_cleanup', None)
        self.save()

    def sync(self, record, now):
        sid = record['id']
        entry = self.state['sessions'].setdefault(sid, {})
        archive_store.recover(self, sid, entry)
        self.recover_move(sid, entry)
        source = Path(record['rollout_path'])
        meta = self.validate_source(source, sid)
        before = signature(source)
        if entry.get('archive_path'):
            archive = Path(entry['archive_path'])
            if archive.is_symlink() or not archive.is_file():
                raise RuntimeError('Session archive unavailable; refusing an empty replacement')
            if (record['archived'] and entry.get('name') == record['display_name']
                    and entry.get('recorded_cwd') == record['cwd']
                    and entry.get('source_path') == str(source)
                    and entry.get('source_signature') == before
                    and entry.get('archive_signature') == signature(archive)):
                entry.update(status='synced')
                entry.pop('error', None)
                entry.pop('missing_since', None)
                return
            archive_store.restore(self, sid, entry)
        dest, marker = self.destination(record, entry)
        target = dest / layout.HISTORY
        if entry.get('source_signature') != before or entry.get('destination_signature') != signature(target):
            digest = self.copy(source, target, before)
            entry.update({'source_signature': before, 'destination_signature': signature(target),
                          'sha256': digest, 'last_synced_at': now})
            LOG.info('Synced %s (%s bytes)', sid, before[2])
        entry.update({'name': record['display_name'], 'source_path': str(source), 'archived': bool(record['archived']),
                      'forked_from_id': meta.get('forked_from_id'), 'status': 'synced'})
        entry.pop('error', None)
        entry.pop('missing_since', None)
        marker.update({'source_path': str(source), 'session_name': entry['name'], 'archived': entry['archived'],
                       'sha256': entry.get('sha256'), 'last_synced_at': entry.get('last_synced_at'), 'status': 'synced'})
        atomic_json(dest / MARKER, marker)
        self.cleanup_legacy(sid, entry)
        layout.update_status(entry)
        if record['archived']:
            archive_store.pack(self, sid, entry)

    def source_ids(self):
        if not (self.home / 'sessions').is_dir():
            raise RuntimeError('Source sessions directory unavailable; deletion suspended')
        ids = set()
        for root in [self.home / 'sessions', self.home / 'archived_sessions']:
            if not root.exists():
                continue
            ensure_plain_dir(root)
            def fail(error):
                raise error
            for parent, dirs, files in os.walk(root, onerror=fail, followlinks=False):
                if any((Path(parent) / name).is_symlink() for name in dirs):
                    raise RuntimeError('Symlink in source tree; deletion suspended')
                for name in files:
                    if name.startswith('rollout-') and name.endswith('.jsonl'):
                        ids.update(re.findall(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', name))
        return ids

    def remove(self, sid, entry):
        archive_store.recover(self, sid, entry)
        if entry.get('archive_path'):
            archive_store.restore(self, sid, entry)
        self.recover_move(sid, entry)
        if not entry.get('directory'):
            return True
        directory = Path(entry['directory'])
        if not directory.exists() and not directory.is_symlink():
            return True
        marker = marker_at(directory, sid)
        if not marker:
            raise RuntimeError('Deletion suspended: missing ownership marker')
        name = marker.get('transcript_file')
        if name:
            self.check_filename(name)
            file = directory / name
            if file.is_symlink():
                raise ValueError('Deletion suspended: symlink transcript')
            file.unlink(missing_ok=True)
        if marker.get('layout_version') == 3:
            if layout.remove_empty_scaffold(directory):
                return True
            entry.update({'status': 'source_deleted', 'id': sid})
            marker['status'] = 'source_deleted'
            atomic_json(directory / MARKER, marker)
            layout.update_status(entry)
            if entry.get('archived'):
                archive_store.pack(self, sid, entry)
            LOG.info('Deleted session history %s; retained workspace files', sid)
            return False
        (directory / MARKER).unlink()
        try:
            directory.rmdir()
        except OSError:
            pass
        return True

    def tick(self, now=None):
        now = time.time() if now is None else now
        records, all_ids, db_identity = read_inventory(self.home)
        if not self.state['initialized']:
            self.state['baseline'] = {sid: r['fingerprint'] for sid, r in records.items()}
            self.state['initialized'] = True
        projects = {e['project_directory'] for e in self.state['sessions'].values() if e.get('project_directory')}
        same_db = self.state.get('database_identity', db_identity) == db_identity
        self.state['database_identity'] = db_identity
        if not same_db:
            for e in self.state['sessions'].values():
                e.pop('missing_since', None)
        for sid, record in records.items():
            if sid in self.seeds or sid in self.state['sessions'] or self.state['baseline'].get(sid) != record['fingerprint']:
                self.state['baseline'].pop(sid, None)
                try:
                    self.sync(record, now)
                except (OSError, ValueError, RuntimeError) as error:
                    self.state['sessions'].setdefault(sid, {}).update({'status': 'retry', 'error': str(error)})
                    LOG.warning('Session %s: %s', sid, error)
        missing = [(sid, e) for sid, e in self.state['sessions'].items() if sid not in all_ids and (e.get('status') != 'source_deleted' or e.get('archive_job'))]
        if missing:
            present = self.source_ids()
            for sid, entry in missing:
                if sid in present or (entry.get('source_path') and Path(entry['source_path']).exists()):
                    entry.pop('missing_since', None)
                    continue
                since = entry.setdefault('missing_since', now)
                if same_db and now - since >= self.grace:
                    try:
                        if self.remove(sid, entry):
                            del self.state['sessions'][sid]
                    except (OSError, ValueError, RuntimeError) as error:
                        entry.update({'status': 'retry', 'error': str(error)})
        projects.update(e['project_directory'] for e in self.state['sessions'].values() if e.get('project_directory'))
        self.state['navigation_errors'] = {}
        for project in projects:
            try:
                entries = [(sid, e) for sid, e in self.state['sessions'].items() if e.get('project_directory') == project]
                layout.update_project_index(Path(project), entries)
            except (OSError, ValueError) as error:
                self.state['navigation_errors'][project] = str(error)
                LOG.warning('Project navigation: %s', error)
        self.state.update({'last_scan_at': now, 'layout_version': 3})
        self.state.pop('scan_error', None)
        self.save()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser())
    p.add_argument('--state-dir', type=Path, default=Path.home() / 'Library/Application Support/CodexSessionMirror/state')
    p.add_argument('--seed', action='append', default=[])
    p.add_argument('--interval', type=float, default=2)
    p.add_argument('--delete-grace', type=float, default=15)
    p.add_argument('--once', action='store_true')
    p.add_argument('--status', action='store_true')
    p.add_argument('--locate', action='store_true', help='Print the current session workspace, without changing it')
    p.add_argument('--session-id', default=os.environ.get('CODEX_THREAD_ID'))
    p.add_argument('--wait', type=float, default=8)
    args = p.parse_args()
    if args.status:
        path = args.state_dir / 'state.json'
        print(path.read_text(encoding='utf-8') if path.exists() else '{"installed": false}')
        return
    if args.locate:
        if not args.session_id or args.wait < 0 or args.wait > 30:
            p.error('locate requires a session ID and wait between 0 and 30 seconds')
        end = time.monotonic() + args.wait
        while True:
            statefile = args.state_dir / 'state.json'
            state = json.loads(statefile.read_text()) if statefile.exists() else {}
            entry = state.get('sessions', {}).get(args.session_id, {})
            if entry.get('archive_path') and not entry.get('archive_job') and Path(entry['archive_path']).is_file():
                print(json.dumps({'session_id': args.session_id, 'directory': None,
                                  'archive': entry['archive_path'], 'status': entry.get('status'),
                                  'archived': True, 'action': 'Unarchive the session in Codex to restore its workspace.'}, ensure_ascii=False))
                return
            if entry.get('layout_version') == 3 and entry.get('directory') and Path(entry['directory']).is_dir():
                print(json.dumps({'session_id': args.session_id, 'directory': entry['directory'],
                                  'status': entry.get('status'), 'notes': str(Path(entry['directory']) / layout.NOTES),
                                  'work_index': str(Path(entry['directory']) / '工作/索引.md')}, ensure_ascii=False))
                return
            if time.monotonic() >= end:
                raise SystemExit('Session workspace not available yet; check the mirror service. Do not guess the directory.')
            time.sleep(0.25)
    if args.interval < 0.5 or args.delete_grace < 0:
        p.error('interval must be >= 0.5; delete-grace must be >= 0')
    os.umask(0o077)
    args.state_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(args.state_dir / 'mirror.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    with (args.state_dir / 'worker.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another mirror worker is already running')
        worker = Mirror(args.codex_home, args.state_dir, args.seed, args.delete_grace)
        running = [True]
        def stop(*_):
            running[0] = False
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while running[0]:
            try:
                worker.tick()
            except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
                LOG.exception('Scan failed; deletion not inferred')
                worker.state['scan_error'] = str(error)
                worker.save()
                if args.once:
                    raise
            if args.once:
                break
            time.sleep(args.interval)


if __name__ == '__main__':
    main()
