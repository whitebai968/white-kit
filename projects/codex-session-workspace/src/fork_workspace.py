"""One-time, verified workspace inheritance. Codex data stays read-only."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import archive_store as archive
import session_layout as layout

CONTROL = {layout.MARKER, layout.HISTORY}
STAMP = '.fork-owner.json'


def parent_id(meta, sid):
    value = meta.get('forked_from_id')
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value) or value == sid:
        raise ValueError('分叉来源 ID 无效，未复制任何父会话文件')
    return value


def payload_snapshot(root):
    return {k: v for k, v in archive.snapshot(root).items() if k not in CONTROL and k != '.'}


def tree_digest(root):
    value = json.dumps(archive.snapshot(root), ensure_ascii=False, sort_keys=True).encode('utf-8')
    return hashlib.sha256(value).hexdigest()


def own_stage(job):
    stage = Path(job['stage'])
    layout.plain_dir(stage)
    stamp = stage / STAMP
    if stamp.is_symlink() or not stamp.is_file() or json.loads(stamp.read_text()).get('token') != job['token']:
        raise RuntimeError('分叉临时目录的所有权不匹配，保留现场')
    return stage


def cleanup_stage(job):
    stage = Path(job['stage'])
    if stage.exists() or stage.is_symlink():
        own_stage(job)
        shutil.rmtree(stage)


def recover(worker, sid, entry):
    job = entry.get('fork_job')
    if not job:
        return
    dest = Path(job['destination'])
    layout.plain_dir(dest.parent)
    if job['phase'] == 'copying':
        cleanup_stage(job)
        entry.pop('fork_job')
        worker.save()
        return
    if job['phase'] != 'prepared':
        raise RuntimeError('未知分叉事务状态，保留现场')
    root = Path(job['stage']) / 'workspace'
    if root.exists():
        own_stage(job)
        if dest.exists() or dest.is_symlink():
            raise RuntimeError('分叉目标目录已被占用，未覆盖已有文件')
        if tree_digest(root) != job['tree_sha256']:
            raise RuntimeError('分叉临时副本发生变化，未发布不完整目录')
        os.rename(root, dest)
        archive.fsync_dir(dest.parent)
    marker_path = dest / layout.MARKER
    layout.plain_dir(dest)
    if marker_path.is_symlink() or not marker_path.is_file():
        raise RuntimeError('无法核对子会话工作区身份')
    marker = json.loads(marker_path.read_text())
    if (marker.get('owner') != layout.OWNER or marker.get('session_id') != sid
            or marker.get('fork_token') != job['token']):
        raise RuntimeError('子会话管理标记不匹配，未覆盖已有目录')
    # Once published, never replay the copy over subsequent child edits.
    entry.update(directory=str(dest), destination_key=job['key'], project_directory=job['project'],
                 layout_version=3, fork_state='complete', forked_from_id=job['parent_id'],
                 fork_snapshot_at=job['snapshot_at'], fork_source=job['parent_source'],
                 fork_warnings=job['warnings'], recorded_cwd=job['cwd'])
    entry.pop('fork_job')
    worker.save()
    cleanup_stage(job)


def read_parent(worker, parent, stage):
    entry = worker.state['sessions'].get(parent)
    if not entry:
        raise RuntimeError('找不到父会话的已同步工作区；请先让父会话完成同步')
    if any(entry.get(key) for key in ('pending_move', 'archive_job', 'fork_job')):
        raise RuntimeError('父会话正在移动、归档或复制，等待其完成后重试')
    if entry.get('status') == 'retry':
        raise RuntimeError('父会话同步异常，请先查看父会话的项目导航错误')
    if entry.get('archive_path'):
        path = Path(entry['archive_path'])
        checksum = archive.digest(path)
        meta = archive.read_archive(path, parent)
        temp = stage / 'parent-archive'
        temp.mkdir()
        root = archive.extract(path, temp, meta)
        if archive.digest(path) != checksum:
            raise RuntimeError('父会话 ZIP 在读取期间变化，稍后重试')
        return root, str(path), (path, checksum), [Path(entry['directory'])] + ([Path(entry['archive_origin_directory'])] if entry.get('archive_origin_directory') else [])
    if not entry.get('directory'):
        raise RuntimeError('父会话尚无可复制的工作区')
    root = layout.plain_dir(Path(entry['directory']))
    marker = root / layout.MARKER
    if marker.is_symlink() or not marker.is_file():
        raise RuntimeError('父会话工作区缺少管理标记')
    data = json.loads(marker.read_text())
    if data.get('owner') != layout.OWNER or data.get('session_id') != parent or data.get('layout_version') != 3:
        raise RuntimeError('父会话工作区身份不匹配，未复制')
    return root, str(root), None, [root]


def link_targets(source, items, original_roots):
    targets = {}
    for rel, item in items.items():
        if item['kind'] != 'link':
            continue
        raw_target = Path(item['target'])
        original_root = next((p for p in original_roots if raw_target.is_absolute() and (raw_target == p or p in raw_target.parents)), None)
        if original_root is not None:
            target = (source / raw_target.relative_to(original_root)).resolve(strict=False)
        else:
            target = (source / rel).resolve(strict=False)
        if target != source and source not in target.parents:
            raise ValueError('外部符号链接无法保证分叉独立，请先处理：' + rel)
        relative_target = target.relative_to(source)
        # Relative targets keep links inside the child; no parent path survives.
        targets[rel] = os.path.relpath(relative_target, Path(rel).parent)
    return targets


def copy_payload(source, dest, items, links):
    dest.mkdir(mode=0o700)
    for rel, item in sorted(items.items(), key=lambda x: len(Path(x[0]).parts)):
        src, dst = source / rel, dest / rel
        if item['kind'] == 'dir':
            dst.mkdir(mode=0o700)
        elif item['kind'] == 'link':
            dst.symlink_to(links[rel])
        else:
            if src.is_symlink():
                raise RuntimeError('复制期间文件变为符号链接：' + rel)
            shutil.copy2(src, dst)
    for rel, item in sorted(items.items(), key=lambda x: len(Path(x[0]).parts), reverse=True):
        dst = dest / rel
        if item['kind'] != 'link':
            os.chmod(dst, item['mode'])
        os.utime(dst, ns=(item['mtime_ns'], item['mtime_ns']), follow_symlinks=False)
    expected = {k: dict(v) for k, v in items.items()}
    for rel, target in links.items():
        expected[rel]['target'] = target
    if payload_snapshot(dest) != expected:
        raise RuntimeError('分叉文件校验失败，未发布副本')


def references(root, source_path, items):
    if source_path.endswith('.zip'):
        source_path = source_path[:-4]
    needle = source_path.encode('utf-8')
    affected = []
    for rel, item in items.items():
        if item['kind'] != 'file' or item['size'] > 1_000_000:
            continue
        if Path(rel).suffix.lower() not in {'.md', '.txt', '.json', '.py', '.sh', '.toml', '.yml', '.yaml'} and Path(rel).name != '.git':
            continue
        data = (root / rel).read_bytes()
        if needle in data or (Path(rel).name == '.git' and data.startswith(b'gitdir:')):
            affected.append(rel)
    return affected[:20]


def initialize(worker, record, entry, meta, source, now):
    sid = record['id']
    recover(worker, sid, entry)
    parent = parent_id(meta, sid)
    entry['forked_from_id'] = parent
    if entry.get('fork_state') in {'complete', 'legacy_preserved'}:
        return
    # Existing workspaces are never retroactively populated from the parent.
    if entry.get('directory') or entry.get('archive_path'):
        entry['fork_state'] = 'legacy_preserved'
        return
    if not parent:
        return
    entry.update(fork_state='pending', status='retry')
    project = layout.plain_dir(Path(entry['project_directory']))
    base = layout.plain_dir(project / '已归档', create=True) if record['archived'] else project
    dest = worker.pick_directory(base, record['display_name'], sid)
    if dest.exists():
        entry.update(directory=str(dest), fork_state='legacy_preserved')
        return
    stage = Path(tempfile.mkdtemp(prefix='.codex-fork-', dir=base))
    job = {'phase': 'copying', 'stage': str(stage), 'destination': str(dest), 'token': uuid.uuid4().hex,
           'parent_id': parent, 'project': str(project), 'key': [str(base), worker.directory_name(record['display_name'], sid)],
           'cwd': record['cwd'], 'snapshot_at': now}
    worker.write_json(stage / STAMP, {'token': job['token']})
    entry['fork_job'] = job
    worker.save()
    try:
        parent_root, parent_source, archive_guard, original_root = read_parent(worker, parent, stage)
        if parent_root == dest or parent_root in dest.parents or parent_root in stage.parents:
            raise RuntimeError('父子目录不能相互嵌套，未执行递归复制')
        before = payload_snapshot(parent_root)
        links = link_targets(parent_root, before, original_root)
        root = stage / 'workspace'
        copy_payload(parent_root, root, before, links)
        if payload_snapshot(parent_root) != before:
            raise RuntimeError('父会话文件在复制期间变化，保留原件并重试')
        if archive_guard and archive.digest(archive_guard[0]) != archive_guard[1]:
            raise RuntimeError('父会话 ZIP 在复制期间变化，稍后重试')
        warnings = references(root, parent_source, before)
        layout.initialize(root)
        copied = dict(entry, directory=str(root), status='synced', fork_state='complete', fork_snapshot_at=now,
                      forked_from_id=parent, fork_warnings=warnings, last_synced_at=now)
        checksum = worker.copy(source, root / layout.HISTORY, worker.file_signature(source))
        worker.write_json(root / layout.MARKER, {'owner': layout.OWNER, 'session_id': sid, 'layout_version': 3,
                          'project_directory': str(project), 'session_name': record['display_name'],
                          'transcript_file': layout.HISTORY, 'source_path': str(source), 'sha256': checksum,
                          'fork_token': job['token'], 'forked_from_id': parent, 'fork_snapshot_at': now})
        layout.update_status(copied)
        job.update(phase='prepared', parent_source=parent_source, warnings=warnings, tree_sha256=tree_digest(root))
        archive.fsync_dir(root)
        archive.fsync_dir(stage)
        worker.save()
    except (OSError, ValueError, RuntimeError):
        if job['phase'] == 'copying':
            cleanup_stage(job)
            entry.pop('fork_job', None)
            worker.save()
        raise
    recover(worker, sid, entry)
