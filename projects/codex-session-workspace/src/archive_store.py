"""Verified ZIP storage with durable, restartable pack/unpack transactions.

Only ordinary files, directories and symlinks are supported. Symlinks are stored
as links, never followed. ZIP metadata describes hashes, modes and nanosecond
mtimes; ACLs, xattrs and hard-link relationships are outside this format.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile
import session_layout as layout

META = '__codex_archive__.json'
FORMAT = 'codex-session-zip-v1'


def digest(path):
    if Path(path).is_symlink():
        raise ValueError('Refusing symlink archive/file')
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def fsync_dir(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def info(path):
    s = path.lstat()
    item = {'mode': stat.S_IMODE(s.st_mode), 'mtime_ns': s.st_mtime_ns}
    if stat.S_ISLNK(s.st_mode):
        item.update(kind='link', target=os.readlink(path))
    elif stat.S_ISDIR(s.st_mode):
        item['kind'] = 'dir'
    elif stat.S_ISREG(s.st_mode):
        item.update(kind='file', size=s.st_size, sha256=digest(path))
    else:
        raise ValueError('Cannot archive special file: ' + str(path))
    after = path.lstat()
    if (s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode):
        raise RuntimeError('File changed during archive inspection: ' + str(path))
    return item


def snapshot(root):
    layout.plain_dir(root)
    items = {'.': info(root)}
    def visit(parent):
        for p in sorted(parent.iterdir()):
            item = info(p)
            items[p.relative_to(root).as_posix()] = item
            if item['kind'] == 'dir':
                visit(p)
    visit(root)
    return items


def valid_rel(name):
    p = PurePosixPath(name)
    if not name or '\\' in name or '\x00' in name or p.is_absolute() or any(x in ('', '.', '..') for x in name.split('/')):
        raise ValueError('Unsafe archive member')
    return p


def read_archive(path, sid):
    """Validate the entire payload before extraction or any original removal."""
    layout.plain_dir(path.parent)
    if path.is_symlink():
        raise ValueError('Refusing symlink archive')
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if len(names) != len(set(names)) or META not in names:
                raise ValueError('Duplicate members or missing archive metadata')
            meta = json.loads(z.read(META))
            if meta.get('format') != FORMAT or meta.get('session_id') != sid:
                raise ValueError('Archive ownership mismatch')
            root = meta['root']
            valid_rel(root)
            if '/' in root or root == META:
                raise ValueError('Invalid archive root')
            items = meta['items']
            if items.get('.', {}).get('kind') != 'dir':
                raise ValueError('Missing archive root directory')
            expected = {META}
            for rel, item in items.items():
                if rel != '.':
                    valid_rel(rel)
                    parent = PurePosixPath(rel).parent
                    while str(parent) != '.':
                        if items.get(str(parent), {}).get('kind') != 'dir':
                            raise ValueError('Archive member traverses a non-directory')
                        parent = parent.parent
                member = root + ('/' + rel if rel != '.' else '')
                kind = item['kind']
                if kind not in ('dir', 'file', 'link'):
                    raise ValueError('Unsupported archive member type')
                member += '/' if kind == 'dir' else ''
                expected.add(member)
                record = z.getinfo(member)
                if not isinstance(item['mode'], int) or not 0 <= item['mode'] <= 0o7777 or not isinstance(item['mtime_ns'], int):
                    raise ValueError('Invalid archive file attributes')
                if kind == 'file':
                    h = hashlib.sha256()
                    with z.open(record) as f:
                        for chunk in iter(lambda: f.read(1024 * 1024), b''):
                            h.update(chunk)
                    if record.file_size != item['size'] or h.hexdigest() != item['sha256']:
                        raise ValueError('Archive file hash mismatch')
                elif kind == 'link':
                    if z.read(record).decode('utf-8') != item['target'] or '\x00' in item['target']:
                        raise ValueError('Invalid archived symlink')
                elif z.read(record) != b'':
                    raise ValueError('Invalid directory payload')
            if expected != set(names):
                raise ValueError('Unexpected or missing archive members')
            if items.get(layout.MARKER, {}).get('kind') != 'file':
                raise ValueError('Archive marker must be a regular file')
            marker = json.loads(z.read(root + '/' + layout.MARKER))
            if marker.get('owner') != layout.OWNER or marker.get('session_id') != sid:
                raise ValueError('Workspace marker does not match archive')
            return meta
    except (zipfile.BadZipFile, KeyError, TypeError, UnicodeError) as e:
        raise ValueError('Invalid session archive: ' + str(e)) from e


def write_archive(directory, target, sid):
    before = snapshot(directory)
    marker = json.loads((directory / layout.MARKER).read_text())
    if marker.get('owner') != layout.OWNER or marker.get('session_id') != sid:
        raise ValueError('Cannot pack an unowned workspace')
    meta = {'format': FORMAT, 'session_id': sid, 'root': target.stem, 'items': before}
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for rel, item in before.items():
            p = directory if rel == '.' else directory / rel
            member = meta['root'] + ('/' + rel if rel != '.' else '')
            kind = item['kind']
            member += '/' if kind == 'dir' else ''
            zi = zipfile.ZipInfo(member)
            zi.create_system = 3
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = (({'dir': stat.S_IFDIR, 'file': stat.S_IFREG, 'link': stat.S_IFLNK}[kind] | item['mode']) << 16)
            if kind == 'file':
                if p.is_symlink():
                    raise RuntimeError('File changed to symlink during packing')
                with p.open('rb') as src, z.open(zi, 'w', force_zip64=True) as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
            else:
                z.writestr(zi, item['target'].encode('utf-8') if kind == 'link' else b'')
        z.writestr(META, json.dumps(meta, ensure_ascii=False))
    with target.open('rb') as f:
        os.fsync(f.fileno())
    read_archive(target, sid)
    fsync_dir(target.parent)
    if snapshot(directory) != before:
        raise RuntimeError('Workspace changed during packing; original retained')
    return meta


def matches(actual, expected, directory=False):
    # Directory mtimes change during incremental removal and crash recovery.
    if directory:
        actual, expected = dict(actual), dict(expected)
        actual.pop('mtime_ns', None)
        expected.pop('mtime_ns', None)
    return actual == expected


def remove_packed_tree(directory, meta):
    """Remove only verified members; an interrupted cleanup is safe to resume."""
    if not directory.exists() and not directory.is_symlink():
        return
    current = snapshot(directory)
    for rel, item in current.items():
        if rel not in meta['items'] or not matches(item, meta['items'][rel], item['kind'] == 'dir'):
            raise RuntimeError('Workspace changed after packing; ZIP and remaining files retained')
    # The marker goes last among files so an interrupted cleanup remains owned.
    leaves = [rel for rel, item in current.items() if item['kind'] != 'dir']
    leaves.sort(key=lambda rel: rel == layout.MARKER)
    for rel in leaves:
        p = directory / rel
        if not matches(info(p), meta['items'][rel]):
            raise RuntimeError('File changed during cleanup; remaining files retained')
        p.unlink()
    dirs = [rel for rel, item in current.items() if item['kind'] == 'dir' and rel != '.']
    for rel in sorted(dirs, key=lambda r: len(PurePosixPath(r).parts), reverse=True):
        (directory / rel).rmdir()
    directory.rmdir()
    fsync_dir(directory.parent)


def extract(path, stage, meta):
    root = stage / meta['root']
    root.mkdir(mode=0o700)
    items = meta['items']
    with zipfile.ZipFile(path) as z:
        for rel, item in sorted(items.items(), key=lambda x: len(PurePosixPath(x[0]).parts)):
            if rel == '.':
                continue
            p = root / rel
            if item['kind'] == 'dir':
                p.mkdir(mode=0o700)
            elif item['kind'] == 'link':
                p.symlink_to(item['target'])
            else:
                with z.open(meta['root'] + '/' + rel) as src, p.open('xb') as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
        for rel, item in sorted(items.items(), key=lambda x: len(PurePosixPath(x[0]).parts), reverse=True):
            p = root if rel == '.' else root / rel
            if item['kind'] != 'link':
                os.chmod(p, item['mode'])
            os.utime(p, ns=(item['mtime_ns'], item['mtime_ns']), follow_symlinks=False)
    if snapshot(root) != items:
        raise RuntimeError('Extracted files differ from archive; ZIP retained')
    fsync_dir(stage)
    return root


def choose_zip(parent, stem, sid):
    for suffix in ('', '--' + sid[-8:], '--' + sid):
        p = parent / (stem + suffix + '.zip')
        if not p.exists() and not p.is_symlink():
            return p
    raise RuntimeError('Archive filenames are occupied; no files overwritten')


def recover(worker, sid, entry):
    job = entry.get('archive_job')
    if not job:
        return
    source, dest = Path(job['source']), Path(job['destination'])
    layout.plain_dir(source.parent)
    layout.plain_dir(dest.parent)
    stage = Path(job['stage'])
    if job['mode'] == 'pack':
        temp = stage / dest.name
        if not dest.exists() and not dest.is_symlink():
            if digest(temp) != job['sha256']:
                raise RuntimeError('Pending archive changed; original retained')
            os.link(temp, dest)  # exclusive publish, never overwrite another ZIP
            fsync_dir(dest.parent)
        if digest(dest) != job['sha256']:
            raise RuntimeError('Published archive changed; original retained')
        meta = read_archive(dest, sid)
        remove_packed_tree(source, meta)
        entry.update(archive_path=str(dest), archive_signature=worker.file_signature(dest))
        entry.pop('archive_job')
        worker.save()
    elif job['mode'] == 'restore':
        root = stage / job['root']
        if root.exists():
            if dest.exists() or dest.is_symlink():
                raise RuntimeError('Restore destination occupied; ZIP retained')
            os.rename(root, dest)
            fsync_dir(dest.parent)
        if snapshot(dest) != job['items']:
            raise RuntimeError('Restored workspace changed before commit; ZIP retained')
        if source.exists() or source.is_symlink():
            if digest(source) != job['sha256']:
                raise RuntimeError('Archive changed during restore; both copies retained')
            source.unlink()
            fsync_dir(source.parent)
        entry['directory'] = str(dest)
        entry.pop('archive_path', None)
        entry.pop('archive_signature', None)
        entry.pop('archive_job')
        worker.save()
    else:
        raise RuntimeError('Unknown archive transaction')
    # This is only our private, temporary archive/extraction container.
    if stage.exists():
        shutil.rmtree(stage)


def pack(worker, sid, entry):
    directory = Path(entry['directory'])
    parent = layout.plain_dir(Path(entry['project_directory']) / '已归档', create=True)
    dest = choose_zip(parent, directory.name, sid)
    stage = Path(tempfile.mkdtemp(prefix='.codex-pack-', dir=parent))
    try:
        temp = stage / dest.name
        write_archive(directory, temp, sid)
        entry['archive_job'] = {'mode': 'pack', 'source': str(directory), 'destination': str(dest),
                                'stage': str(stage), 'sha256': digest(temp)}
        worker.save()
    except BaseException:
        if not entry.get('archive_job'):
            shutil.rmtree(stage)
        raise
    recover(worker, sid, entry)


def restore(worker, sid, entry):
    archive = Path(entry['archive_path'])
    original_hash = digest(archive)
    meta = read_archive(archive, sid)
    dest = Path(entry['directory'])
    layout.plain_dir(dest.parent)
    if dest.exists() or dest.is_symlink():
        dest = None
        for suffix in ('--' + sid[-8:], '--' + sid):
            candidate = archive.parent / (meta['root'] + suffix)
            if not candidate.exists() and not candidate.is_symlink():
                dest = candidate
                break
        if dest is None:
            raise RuntimeError('Restore workspace names are occupied')
    stage = Path(tempfile.mkdtemp(prefix='.codex-restore-', dir=archive.parent))
    try:
        extract(archive, stage, meta)
        if digest(archive) != original_hash:
            raise RuntimeError('Archive changed during extraction')
        entry['archive_job'] = {'mode': 'restore', 'source': str(archive), 'destination': str(dest),
                                'stage': str(stage), 'sha256': original_hash,
                                'root': meta['root'], 'items': meta['items']}
        worker.save()
    except BaseException:
        if not entry.get('archive_job'):
            shutil.rmtree(stage)
        raise
    recover(worker, sid, entry)
