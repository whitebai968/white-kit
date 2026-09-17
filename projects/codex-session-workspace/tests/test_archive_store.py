import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
import zipfile
import archive_store as a
import session_mirror as m
import test_session_mirror as fixture


class ArchiveTests(unittest.TestCase):
    setUp = fixture.SessionWorkspaceTests.setUp
    add = fixture.SessionWorkspaceTests.add
    update = fixture.SessionWorkspaceTests.update
    dest = fixture.SessionWorkspaceTests.dest
    delete = fixture.SessionWorkspaceTests.delete
    assert_copy = fixture.SessionWorkspaceTests.assert_copy

    def entry(self):
        return self.worker.state['sessions'][self.sid]

    def archive(self):
        return Path(self.entry()['archive_path'])

    def prepare(self):
        self.worker.tick(0)
        for area in ['资料', '工作', '成果']:
            (self.dest() / area / 'file.txt').write_text(area)
        return self.dest()

    def pack(self):
        self.update(archived=1)
        self.worker.tick(2)
        self.assertEqual(self.entry()['status'], 'synced', self.entry().get('error'))
        return self.archive()

    def restart(self, now=4):
        self.worker = m.Mirror(self.home, self.state_dir, grace=10)
        self.worker.tick(now)

    def test_files_empty_dirs_modes_mtimes_and_symlinks_round_trip(self):
        old = self.prepare()
        (old / '工作/empty').mkdir()
        executable = old / '工作/run.sh'
        executable.write_text('#!/bin/sh\necho hello\n')
        executable.chmod(0o751)
        os.utime(executable, ns=(1700000000123456789, 1700000000123456789))
        (old / '工作/run-link').symlink_to('run.sh')
        (old / '工作/broken').symlink_to('nonexistent')
        external = self.root / 'external.txt'
        external.write_text('outside stays outside')
        (old / '工作/outside').symlink_to(external)
        expected = a.snapshot(old)
        archive = self.pack()
        self.assertFalse(old.exists())
        meta = a.read_archive(archive, self.sid)
        self.assertEqual(meta['root'], 'A')
        self.assertNotIn('outside stays outside', archive.read_bytes().decode('latin1'))
        self.update(archived=0)
        self.worker.tick(6)
        self.assertFalse(archive.exists())
        got = a.snapshot(self.dest())
        # Notes and ownership metadata reflect the lifecycle; user files stay exact.
        for rel, item in expected.items():
            if rel not in ('.', m.MARKER, m.layout.NOTES, m.layout.HISTORY):
                self.assertEqual(got[rel], item, rel)
        self.assertEqual(external.read_text(), 'outside stays outside')

    def test_archive_rename_updates_zip_and_internal_root(self):
        self.prepare()
        old = self.pack()
        self.update(name='新名字')
        self.worker.tick(4)
        self.assertEqual(self.archive().name, '新名字.zip')
        self.assertFalse(old.exists())
        self.assertEqual(a.read_archive(self.archive(), self.sid)['root'], '新名字')
        with zipfile.ZipFile(self.archive()) as z:
            self.assertIn('新名字/工作/file.txt', z.namelist())

    def test_zip_collision_keeps_existing_zip(self):
        self.prepare()
        base = self.project / '已归档'
        base.mkdir()
        occupied = base / 'A.zip'
        occupied.write_bytes(b'personal zip')
        self.pack()
        self.assertEqual(occupied.read_bytes(), b'personal zip')
        self.assertEqual(self.archive().name, 'A--' + self.sid[-8:] + '.zip')
        self.assertEqual(a.read_archive(self.archive(), self.sid)['root'], self.archive().stem)

    def test_restore_does_not_overwrite_active_name_collision(self):
        old = self.prepare()
        archive = self.pack()
        old.mkdir()
        (old / 'unrelated.txt').write_text('keep')
        self.update(archived=0)
        self.worker.tick(4)
        self.assertEqual((old / 'unrelated.txt').read_text(), 'keep')
        self.assertNotEqual(self.dest(), old)
        self.assertEqual((self.dest() / '工作/file.txt').read_text(), '工作')
        self.assertFalse(archive.exists())

    def test_restore_does_not_overwrite_archived_directory_collision(self):
        self.prepare()
        self.pack()
        occupied = self.dest()
        occupied.mkdir()
        (occupied / 'manual.txt').write_text('keep')
        self.update(archived=0)
        self.worker.tick(4)
        self.assertEqual((occupied / 'manual.txt').read_text(), 'keep')
        self.assertEqual((self.dest() / '成果/file.txt').read_text(), '成果')

    def test_unchanged_archive_is_not_unpacked_or_recompressed(self):
        self.prepare()
        archive = self.pack()
        sig = m.signature(archive)
        with patch.object(a, 'restore', side_effect=AssertionError('unexpected extraction')):
            self.worker.tick(4)
            self.worker.tick(6)
        self.assertEqual(sig, m.signature(archive))

    def test_archived_transcript_update_is_copied(self):
        self.prepare()
        self.pack()
        with self.source.open('ab') as f:
            f.write(b'{"new":true}\n')
        self.worker.tick(4)
        with zipfile.ZipFile(self.archive()) as z:
            self.assertEqual(z.read('A/' + m.layout.HISTORY), self.source.read_bytes())
            self.assertEqual(z.read('A/资料/file.txt').decode(), '资料')

    def test_delete_archived_source_keeps_user_content_in_zip(self):
        self.prepare()
        self.pack()
        self.delete()
        self.worker.tick(4)
        self.worker.tick(15)
        self.assertEqual(self.entry()['status'], 'source_deleted')
        with zipfile.ZipFile(self.archive()) as z:
            self.assertNotIn('A/' + m.layout.HISTORY, z.namelist())
            self.assertEqual(z.read('A/成果/file.txt').decode(), '成果')
            self.assertIn('源会话已删除', z.read('A/' + m.layout.NOTES).decode())

    def test_delete_empty_archived_scaffold_removes_zip(self):
        self.worker.tick(0)
        archive = self.pack()
        self.delete()
        self.worker.tick(4)
        self.worker.tick(15)
        self.assertFalse(archive.exists())
        self.assertNotIn(self.sid, self.worker.state['sessions'])

    def test_damaged_zip_is_retained_and_never_restored(self):
        self.prepare()
        archive = self.pack()
        archive.write_bytes(b'corrupt zip')
        self.update(archived=0)
        self.worker.tick(4)
        self.assertEqual(archive.read_bytes(), b'corrupt zip')
        self.assertEqual(self.entry()['status'], 'retry')
        self.assertFalse((self.project / 'A').exists())

    def test_missing_zip_is_not_replaced_with_empty_workspace(self):
        self.prepare()
        archive = self.pack()
        archive.rename(archive.with_suffix('.saved'))
        self.worker.tick(4)
        self.assertEqual(self.entry()['status'], 'retry')
        self.assertFalse(self.dest().exists())

    def test_archive_navigation_and_locate_point_to_zip(self):
        self.prepare()
        archive = self.pack()
        nav = (self.project / '项目导航.md').read_text()
        self.assertIn('A.zip', nav)
        result = subprocess.run([sys.executable, m.__file__, '--state-dir', str(self.state_dir), '--locate', '--session-id', self.sid, '--wait', '0'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['archive'], str(archive))
        self.assertIsNone(data['directory'])

    def test_upgrade_existing_v1_archived_directory(self):
        old = self.prepare()
        base = self.project / '已归档'
        base.mkdir()
        dest = base / 'A'
        old.rename(dest)
        self.entry().update(directory=str(dest), destination_key=[str(base), 'A'], archived=True)
        self.worker.save()
        self.pack()
        self.assertFalse(dest.exists())
        self.assertEqual(self.archive(), base / 'A.zip')

    def test_restart_after_zip_publish(self):
        self.prepare()
        self.update(archived=1)
        publish = os.link
        def crash(src, dst):
            publish(src, dst)
            raise OSError('crash after publish')
        with patch.object(a.os, 'link', side_effect=crash):
            self.worker.tick(2)
        self.assertIn('archive_job', self.entry())
        self.restart()
        self.assertNotIn('archive_job', self.entry())
        self.assertEqual(self.entry()['status'], 'synced')
        self.assertFalse(self.dest().exists())
        a.read_archive(self.archive(), self.sid)

    def test_restart_during_incremental_original_cleanup(self):
        self.prepare()
        self.update(archived=1)
        unlink = Path.unlink
        def crash(p, *args, **kwargs):
            result = unlink(p, *args, **kwargs)
            if p.name == 'file.txt':
                raise OSError('crash after one original file removed')
            return result
        with patch.object(Path, 'unlink', crash):
            self.worker.tick(2)
        self.assertIn('archive_job', self.entry())
        self.restart()
        with zipfile.ZipFile(self.archive()) as z:
            self.assertEqual(z.read('A/工作/file.txt').decode(), '工作')
        self.assertFalse(self.dest().exists())

    def test_restart_after_extracted_directory_publish(self):
        self.prepare()
        archive = self.pack()
        self.update(archived=0)
        rename = os.rename
        def crash(src, dst):
            rename(src, dst)
            raise OSError('crash after extracted tree rename')
        with patch.object(a.os, 'rename', side_effect=crash):
            self.worker.tick(4)
        self.assertEqual(self.entry()['archive_job']['mode'], 'restore')
        self.restart(6)
        self.assertFalse(archive.exists())
        self.assertEqual((self.dest() / '工作/file.txt').read_text(), '工作')

    def test_restart_after_zip_removed_during_restore(self):
        self.prepare()
        archive = self.pack()
        self.update(archived=0)
        unlink = Path.unlink
        def crash(p, *args, **kwargs):
            result = unlink(p, *args, **kwargs)
            if p == archive:
                raise OSError('crash after zip removed')
            return result
        with patch.object(Path, 'unlink', crash):
            self.worker.tick(4)
        self.assertEqual(self.entry()['archive_job']['mode'], 'restore')
        self.restart(6)
        self.assertEqual((self.dest() / '成果/file.txt').read_text(), '成果')
        self.assertNotIn('archive_path', self.entry())

    def test_changed_original_after_pack_is_retained(self):
        self.prepare()
        self.update(archived=1)
        remove = a.remove_packed_tree
        def changed(directory, meta):
            (directory / '工作/file.txt').write_text('new user edit')
            return remove(directory, meta)
        with patch.object(a, 'remove_packed_tree', side_effect=changed):
            self.worker.tick(2)
        self.assertEqual(self.entry()['status'], 'retry')
        self.assertEqual((self.dest() / '工作/file.txt').read_text(), 'new user edit')
        published = Path(self.entry()['archive_job']['destination'])
        a.read_archive(published, self.sid)
        self.restart()
        self.assertEqual((self.dest() / '工作/file.txt').read_text(), 'new user edit')

    def test_source_changes_during_zip_creation_keep_full_original(self):
        self.prepare()
        self.update(archived=1)
        validate = a.read_archive
        def changed(path, sid):
            result = validate(path, sid)
            (self.dest() / '工作/file.txt').write_text('changed while packing')
            return result
        with patch.object(a, 'read_archive', side_effect=changed):
            self.worker.tick(2)
        self.assertEqual(self.entry()['status'], 'retry')
        self.assertEqual((self.dest() / '工作/file.txt').read_text(), 'changed while packing')
        self.assertFalse(list((self.project / '已归档').glob('*.zip')))
        self.worker.tick(4)
        with zipfile.ZipFile(self.archive()) as z:
            self.assertEqual(z.read('A/工作/file.txt'), b'changed while packing')

    def test_unsafe_zip_members_refused_before_extraction(self):
        self.prepare()
        archive = self.pack()
        with zipfile.ZipFile(archive, 'a') as z:
            z.writestr('../escape.txt', 'bad')
        self.update(archived=0)
        self.worker.tick(4)
        self.assertEqual(self.entry()['status'], 'retry')
        self.assertFalse((archive.parent.parent / 'escape.txt').exists())
        self.assertTrue(archive.exists())

    def test_payload_hash_tampering_refused(self):
        self.prepare()
        archive = self.pack()
        tmp = archive.with_suffix('.tmp')
        with zipfile.ZipFile(archive) as src, zipfile.ZipFile(tmp, 'w') as dst:
            for member in src.infolist():
                data = src.read(member)
                if member.filename == 'A/成果/file.txt':
                    data = b'tampered'
                dst.writestr(member, data)
        tmp.replace(archive)
        self.update(archived=0)
        self.worker.tick(4)
        self.assertEqual(self.entry()['status'], 'retry')
        self.assertTrue(archive.exists())

    def test_special_files_pause_archiving_without_losing_files(self):
        self.prepare()
        os.mkfifo(self.dest() / '工作/pipe')
        self.update(archived=1)
        self.worker.tick(2)
        self.assertEqual(self.entry()['status'], 'retry')
        self.assertEqual((self.dest() / '成果/file.txt').read_text(), '成果')

    def test_archived_session_cwd_inside_workspace_still_restores(self):
        old = self.prepare()
        self.update(cwd=str(old / '工作'))
        self.worker.tick(1)
        self.pack()
        self.worker.tick(4)
        self.update(archived=0)
        self.worker.tick(6)
        self.assertEqual(self.entry()['status'], 'synced')
        self.assertEqual(self.dest(), self.project / 'A')


if __name__ == '__main__':
    unittest.main(verbosity=2)
