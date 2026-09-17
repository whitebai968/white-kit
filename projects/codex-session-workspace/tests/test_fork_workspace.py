import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid
import archive_store
import fork_workspace as f
import session_mirror as m
import test_session_mirror as fixture


class ForkTests(unittest.TestCase):
    setUp = fixture.SessionWorkspaceTests.setUp
    add = fixture.SessionWorkspaceTests.add
    update = fixture.SessionWorkspaceTests.update
    dest = fixture.SessionWorkspaceTests.dest

    def prepare(self):
        self.worker.tick(0)
        for area in ['资料', '工作', '成果']:
            (self.dest() / area / 'file.txt').write_text(area)
        (self.dest() / '工作/empty').mkdir()
        note = self.dest() / m.layout.NOTES
        note.write_text(note.read_text().replace('待 Agent 根据本 session 的实际工作补充。', '父会话已确定目标。'))
        (self.dest() / '工作/索引.md').write_text('# 工作索引\n[file](file.txt)\n')
        return self.dest()

    def child(self, name='B', parent=None, **kwargs):
        return self.add(name, parent=parent or self.sid, **kwargs)

    def entry(self, sid):
        return self.worker.state['sessions'][sid]

    def restart(self, now=4):
        self.worker = m.Mirror(self.home, self.state_dir, grace=10)
        self.worker.tick(now)

    def test_full_copy_includes_notes_index_user_files_and_empty_dirs(self):
        parent = self.prepare()
        b, source = self.child()
        self.worker.tick(2)
        child = self.dest(b)
        for area in ['资料', '工作', '成果']:
            a, c = parent / area / 'file.txt', child / area / 'file.txt'
            self.assertEqual(a.read_bytes(), c.read_bytes())
            self.assertNotEqual(a.stat().st_ino, c.stat().st_ino)
        self.assertTrue((child / '工作/empty').is_dir())
        self.assertEqual((child / '工作/索引.md').read_bytes(), (parent / '工作/索引.md').read_bytes())
        self.assertIn('父会话已确定目标。', (child / m.layout.NOTES).read_text())
        self.assertIn('已一次性复制父工作区', (child / m.layout.NOTES).read_text())
        self.assertEqual((child / m.layout.HISTORY).read_bytes(), source.read_bytes())
        marker = json.loads((child / m.MARKER).read_text())
        self.assertEqual(marker['session_id'], b)
        self.assertEqual(marker['source_path'], str(source))
        self.assertEqual(marker['forked_from_id'], self.sid)
        self.assertEqual(json.loads((parent / m.MARKER).read_text())['session_id'], self.sid)

    def test_restart_never_replays_copy_over_child_edits(self):
        parent = self.prepare()
        b, _ = self.child()
        self.worker.tick(2)
        (self.dest(b) / '工作/file.txt').write_text('child version')
        (parent / '工作/file.txt').write_text('new parent version')
        self.restart()
        self.assertEqual((self.dest(b) / '工作/file.txt').read_text(), 'child version')
        self.assertEqual((parent / '工作/file.txt').read_text(), 'new parent version')

    def test_new_generation_forks_copy_the_immediate_parent(self):
        self.prepare()
        b, _ = self.child()
        self.worker.tick(2)
        (self.dest(b) / '工作/file.txt').write_text('B work')
        c, _ = self.child('C', parent=b)
        self.worker.tick(4)
        self.assertEqual((self.dest(c) / '工作/file.txt').read_text(), 'B work')
        self.assertEqual(self.entry(c)['forked_from_id'], b)

    def test_archived_parent_is_copied_without_restoring_or_modifying_zip(self):
        self.prepare()
        # Absolute internal symlink was created before archiving; remap inside child.
        (self.dest() / '工作/link').symlink_to(self.dest() / '工作/file.txt')
        self.update(archived=1)
        self.worker.tick(2)
        archive = Path(self.entry(self.sid)['archive_path'])
        before = archive.read_bytes()
        b, _ = self.child()
        self.worker.tick(4)
        self.assertEqual(self.entry(b)['status'], 'synced', self.entry(b).get('error'))
        self.assertEqual((self.dest(b) / '成果/file.txt').read_text(), '成果')
        self.assertEqual((self.dest(b) / '工作/link').read_text(), '工作')
        self.assertEqual(archive.read_bytes(), before)
        self.assertFalse(self.dest().exists())

    def test_missing_parent_reports_in_navigation_without_empty_child(self):
        self.prepare()
        b, _ = self.child(parent=str(uuid.uuid4()))
        self.worker.tick(2)
        self.assertEqual(self.entry(b)['status'], 'retry')
        self.assertFalse((self.project / 'B').exists())
        nav = (self.project / '项目导航.md').read_text()
        self.assertIn('分叉复制失败', nav)
        self.assertIn(b, nav)
        self.assertIn('找不到父会话', nav)
        self.assertFalse(list(self.project.glob('.codex-fork-*')))
        result = subprocess.run([sys.executable, m.__file__, '--state-dir', str(self.state_dir), '--locate', '--session-id', b, '--wait', '0'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = json.loads(result.stdout)
        self.assertIsNone(result['directory'])
        self.assertEqual(result['navigation'], str(self.project / '项目导航.md'))
        self.assertIn('找不到父会话', result['error'])

    def test_failure_clears_after_parent_becomes_available(self):
        self.prepare()
        b, _ = self.child()
        parent = self.worker.state['sessions'].pop(self.sid)
        # Keep parent dormant in baseline for one scan.
        records, _, _ = m.read_inventory(self.home)
        self.worker.state['baseline'][self.sid] = records[self.sid]['fingerprint']
        self.worker.seeds.clear()
        self.worker.tick(2)
        self.assertIn('error', self.entry(b))
        self.worker.state['sessions'][self.sid] = parent
        self.worker.tick(4)
        self.assertEqual(self.entry(b)['status'], 'synced')
        self.assertNotIn('error', self.entry(b))
        self.assertNotIn('找不到父会话', (self.project / '项目导航.md').read_text())

    def test_invalid_self_parent_never_copies(self):
        self.prepare()
        b, source = self.child()
        first = json.loads(source.read_text())
        first['payload']['forked_from_id'] = b
        source.write_text(json.dumps(first) + '\n')
        self.worker.tick(2)
        self.assertEqual(self.entry(b)['status'], 'retry')
        self.assertIn('分叉来源 ID 无效', (self.project / '项目导航.md').read_text())
        self.assertFalse((self.project / 'B').exists())

    def test_legacy_child_content_is_not_backfilled(self):
        self.prepare()
        b, _ = self.child()
        with patch.object(f, 'initialize'):
            self.worker.tick(2)
        self.assertFalse((self.dest(b) / '工作/file.txt').exists())
        (self.dest(b) / '工作/own.txt').write_text('legacy child')
        self.restart()
        self.assertEqual(self.entry(b)['fork_state'], 'legacy_preserved')
        self.assertFalse((self.dest(b) / '工作/file.txt').exists())
        self.assertEqual((self.dest(b) / '工作/own.txt').read_text(), 'legacy child')

    def test_parent_changes_during_copy_retry_without_partial_directory(self):
        parent = self.prepare()
        b, _ = self.child()
        copy = f.copy_payload
        def mutate(*args):
            copy(*args)
            (parent / '工作/file.txt').write_text('changed')
        with patch.object(f, 'copy_payload', side_effect=mutate):
            self.worker.tick(2)
        self.assertEqual(self.entry(b)['status'], 'retry')
        self.assertFalse((self.project / 'B').exists())
        self.assertFalse(list(self.project.glob('.codex-fork-*')))
        self.worker.tick(4)
        self.assertEqual((self.dest(b) / '工作/file.txt').read_text(), 'changed')

    def test_copy_failure_preserves_parent_and_reports_reason(self):
        parent = self.prepare()
        before = f.payload_snapshot(parent)
        b, _ = self.child()
        with patch.object(f, 'copy_payload', side_effect=OSError('No space left on device')):
            self.worker.tick(2)
        self.assertEqual(f.payload_snapshot(parent), before)
        self.assertFalse((self.project / 'B').exists())
        self.assertIn('No space left', (self.project / '项目导航.md').read_text())

    def test_restart_recovers_partial_copy(self):
        self.prepare()
        b, _ = self.child()
        with patch.object(f, 'copy_payload', side_effect=SystemExit('power interruption')):
            with self.assertRaises(SystemExit):
                self.worker.tick(2)
        self.assertEqual(self.entry(b)['fork_job']['phase'], 'copying')
        self.restart()
        self.assertEqual((self.dest(b) / '成果/file.txt').read_text(), '成果')
        self.assertFalse(list(self.project.glob('.codex-fork-*')))

    def test_restart_after_publish_preserves_child_edits(self):
        self.prepare()
        b, _ = self.child()
        rename = os.rename
        def crash(src, dst):
            rename(src, dst)
            if '.codex-fork-' in str(src):
                (Path(dst) / '工作/file.txt').write_text('child edited after publish')
                raise OSError('crash after publishing child')
        with patch.object(f.os, 'rename', side_effect=crash):
            self.worker.tick(2)
        self.assertIn('fork_job', self.entry(b))
        self.restart()
        self.assertEqual((self.dest(b) / '工作/file.txt').read_text(), 'child edited after publish')
        self.assertNotIn('fork_job', self.entry(b))

    def test_name_collision_never_overwrites_unrelated_directory(self):
        self.prepare()
        occupied = self.project / 'B'
        occupied.mkdir();(occupied / 'user.txt').write_text('keep')
        b, _ = self.child()
        self.worker.tick(2)
        self.assertNotEqual(self.dest(b), occupied)
        self.assertEqual((occupied / 'user.txt').read_text(), 'keep')
        self.assertEqual((self.dest(b) / '工作/file.txt').read_text(), '工作')

    def test_internal_symlinks_point_only_inside_child(self):
        parent = self.prepare()
        (parent / '工作/relative').symlink_to('file.txt')
        (parent / '工作/absolute').symlink_to(parent / '工作/file.txt')
        b, _ = self.child()
        self.worker.tick(2)
        child = self.dest(b)
        for name in ['relative', 'absolute']:
            self.assertEqual((child / '工作' / name).resolve(), child / '工作/file.txt')
        (child / '工作/absolute').write_text('child only')
        self.assertEqual((parent / '工作/file.txt').read_text(), '工作')

    def test_external_symlink_blocks_copy_and_names_problem_file(self):
        parent = self.prepare()
        external = self.root / 'external.txt';external.write_text('untouched')
        (parent / '工作/external').symlink_to(external)
        b, _ = self.child()
        self.worker.tick(2)
        self.assertEqual(self.entry(b)['status'], 'retry')
        self.assertFalse((self.project / 'B').exists())
        self.assertIn('工作/external', (self.project / '项目导航.md').read_text())
        self.assertEqual(external.read_text(), 'untouched')

    def test_absolute_business_references_warn_without_rewriting(self):
        parent = self.prepare()
        body = 'Report at ' + str(parent / '工作/file.txt')
        (parent / '工作/reference.md').write_text(body)
        b, _ = self.child()
        self.worker.tick(2)
        self.assertEqual((self.dest(b) / '工作/reference.md').read_text(), body)
        self.assertIn('工作/reference.md', self.entry(b)['fork_warnings'])
        self.assertIn('分叉引用待检查', (self.project / '项目导航.md').read_text())

    def test_fork_can_use_an_independent_project(self):
        self.prepare()
        other = self.root / 'other-project';other.mkdir()
        b, _ = self.child(project=other)
        self.worker.tick(2)
        self.assertEqual(self.dest(b), other / 'B')
        self.assertEqual((self.dest(b) / '资料/file.txt').read_text(), '资料')

    def test_child_lifecycle_does_not_change_parent_artifacts(self):
        parent = self.prepare()
        b, source = self.child()
        self.worker.tick(2)
        before = f.payload_snapshot(parent)
        self.update(b, name='renamed-B', archived=1)
        self.worker.tick(4)
        archive = Path(self.entry(b)['archive_path'])
        archive_store.read_archive(archive, b)
        self.update(b, archived=0)
        self.worker.tick(6)
        self.assertEqual((self.dest(b) / '成果/file.txt').read_text(), '成果')
        self.assertEqual(f.payload_snapshot(parent), before)

    def test_scan_error_is_visible_and_clears_on_success(self):
        self.prepare()
        self.worker.report_scan_error(RuntimeError('Database unavailable'))
        self.assertIn('全局同步异常', (self.project / '项目导航.md').read_text())
        self.worker.tick(2)
        self.assertNotIn('Database unavailable', (self.project / '项目导航.md').read_text())

    def test_fork_rejects_parent_marker_with_wrong_identity(self):
        parent = self.prepare()
        b, _ = self.child()
        self.worker.seeds.clear()
        marker = parent / m.MARKER
        data = json.loads(marker.read_text());data['session_id'] = str(uuid.uuid4());marker.write_text(json.dumps(data))
        self.worker.tick(2)
        self.assertEqual(self.entry(b)['status'], 'retry')
        self.assertFalse((self.project / 'B').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
