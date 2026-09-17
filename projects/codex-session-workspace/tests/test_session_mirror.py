import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid
import zipfile
import session_mirror as m


class SessionWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / 'codex'
        (self.home / 'sessions/2026/09/17').mkdir(parents=True)
        (self.home / 'archived_sessions').mkdir()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.state_dir = self.root / 'state'
        self.db = self.home / 'state_5.sqlite'
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT, title TEXT, cwd TEXT, rollout_path TEXT, archived INTEGER, updated_at_ms INTEGER, source TEXT, thread_source TEXT)')
        self.sid, self.source = self.add('A')
        self.worker = m.Mirror(self.home, self.state_dir, [self.sid], grace=10)

    def add(self, name, project=None, parent=None, source='vscode'):
        sid = str(uuid.uuid4())
        cwd = project or self.project
        file = self.home / 'sessions/2026/09/17' / ('rollout-2026-09-17T10-00-00-' + sid + '.jsonl')
        file.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': sid, 'cwd': str(cwd), 'forked_from_id': parent}}) + '\n')
        with sqlite3.connect(self.db) as c:
            c.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)', (sid, name, name, str(cwd), str(file), 0, 1, source, 'user'))
        return sid, file

    def update(self, sid=None, **fields):
        with sqlite3.connect(self.db) as c:
            c.execute('UPDATE threads SET ' + ','.join(k + '=?' for k in fields) + ' WHERE id=?', (*fields.values(), sid or self.sid))

    def dest(self, sid=None):
        return Path(self.worker.state['sessions'][sid or self.sid]['directory'])

    def assert_copy(self, sid=None, source=None):
        self.assertEqual((self.dest(sid) / m.layout.HISTORY).read_bytes(), (source or self.source).read_bytes())

    def delete(self):
        self.source.unlink()
        with sqlite3.connect(self.db) as c:
            c.execute('DELETE FROM threads WHERE id=?', (self.sid,))

    def test_create_append_and_exact_raw_bytes(self):
        self.worker.tick(0)
        self.assertEqual(self.dest(), self.project / 'A')
        self.assert_copy()
        with self.source.open('ab') as f:
            f.write(b'{"unfinished":')
        self.worker.tick(2)
        self.assert_copy()
        with self.source.open('ab') as f:
            f.write(b'true}\n')
        self.worker.tick(4)
        self.assert_copy()
        self.assertTrue((self.dest() / '工作/索引.md').is_file())
        self.assertEqual(list((self.dest() / '资料').iterdir()), [])
        self.assertEqual(list((self.dest() / '成果').iterdir()), [])

    def test_sessions_and_forks_have_independent_workspaces(self):
        self.worker.tick(0)
        (self.dest() / '工作/only-in-A.txt').write_text('A')
        b, bs = self.add('B')
        child, cs = self.add('A-fork', parent=self.sid)
        self.worker.tick(2)
        self.assertEqual(self.dest(b), self.project / 'B')
        self.assertEqual(self.dest(child), self.project / 'A-fork')
        self.assertFalse((self.dest(child) / '工作/only-in-A.txt').exists())
        self.assert_copy(b, bs)
        self.assert_copy(child, cs)
        self.assertIn(self.sid, (self.dest(child) / m.layout.NOTES).read_text())

    def test_whole_workspace_rename_preserves_relative_files(self):
        self.worker.tick(0)
        for area in ['资料', '工作', '成果']:
            (self.dest() / area / 'file.txt').write_text(area)
        self.update(name='renamed')
        self.worker.tick(2)
        self.assertFalse((self.project / 'A').exists())
        for area in ['资料', '工作', '成果']:
            self.assertEqual((self.dest() / area / 'file.txt').read_text(), area)
        self.assert_copy()

    def test_archive_and_restore_move_whole_package(self):
        self.worker.tick(0)
        (self.dest() / '成果/report.txt').write_text('approved')
        archive_source = self.home / 'archived_sessions' / self.source.name
        self.source.rename(archive_source)
        self.source = archive_source
        self.update(archived=1, rollout_path=str(self.source))
        self.worker.tick(2)
        archive = Path(self.worker.state['sessions'][self.sid]['archive_path'])
        self.assertEqual(archive, self.project / '已归档/A.zip')
        self.assertFalse(self.dest().exists())
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(z.read('A/成果/report.txt'), b'approved')
        self.update(name='archived-rename')
        self.worker.tick(4)
        self.assertEqual(Path(self.worker.state['sessions'][self.sid]['archive_path']).name, 'archived-rename.zip')
        self.update(archived=0)
        self.worker.tick(6)
        self.assertEqual(self.dest(), self.project / 'archived-rename')
        self.assertEqual((self.dest() / '成果/report.txt').read_text(), 'approved')
        self.assert_copy()

    def test_delete_empty_session_removes_only_generated_scaffold(self):
        self.worker.tick(0)
        directory = self.dest()
        self.delete()
        self.worker.tick(2)
        self.assertTrue(directory.exists())
        self.worker.tick(13)
        self.assertFalse(directory.exists())

    def test_delete_with_input_work_or_results_keeps_all_files(self):
        self.worker.tick(0)
        directory = self.dest()
        for area in ['资料', '工作', '成果']:
            (directory / area / 'file.txt').write_text(area)
        self.delete()
        self.worker.tick(2)
        self.worker.tick(13)
        self.assertFalse((directory / m.layout.HISTORY).exists())
        for area in ['资料', '工作', '成果']:
            self.assertEqual((directory / area / 'file.txt').read_text(), area)
        self.assertEqual(self.worker.state['sessions'][self.sid]['status'], 'source_deleted')
        self.worker.tick(30)
        self.assertIn('源会话已删除', (directory / m.layout.NOTES).read_text())

    def test_user_edited_notes_and_work_index_are_not_overwritten(self):
        self.worker.tick(0)
        note = self.dest() / m.layout.NOTES
        note.write_text(note.read_text().replace('待 Agent 根据本 session 的实际工作补充。', '用户已确认的决定。'))
        index = self.dest() / '工作/索引.md'
        index.write_text('# 我的工作索引\n[最新结果](group/report.md)\n')
        self.worker.tick(2)
        self.assertIn('用户已确认的决定。', note.read_text())
        self.assertEqual(index.read_text(), '# 我的工作索引\n[最新结果](group/report.md)\n')
        self.delete()
        self.worker.tick(4)
        self.worker.tick(15)
        self.assertTrue(note.exists())

    def test_name_conflicts_never_overwrite_user_directory(self):
        (self.project / 'A').mkdir()
        (self.project / 'A/user.txt').write_text('keep')
        self.worker.tick(0)
        b, bs = self.add('A')
        self.worker.tick(2)
        self.assertNotEqual(self.dest(), self.dest(b))
        self.assertEqual((self.project / 'A/user.txt').read_text(), 'keep')
        self.assert_copy(b, bs)

    def test_user_notes_after_automatic_status_are_retained_on_delete(self):
        self.worker.tick(0)
        directory = self.dest()
        doc = directory / m.layout.NOTES
        doc.write_text(doc.read_text() + '\n用户在自动区后面添加的内容。\n')
        self.delete()
        self.worker.tick(2)
        self.worker.tick(13)
        self.assertTrue(doc.exists())
        self.assertIn('用户在自动区后面添加的内容。', doc.read_text())

    def test_symlinks_do_not_redirect_writes(self):
        external = self.root / 'external'
        external.mkdir()
        (self.project / 'A').symlink_to(external, target_is_directory=True)
        self.worker.tick(0)
        target = self.dest() / m.layout.HISTORY
        target.unlink()
        victim = external / 'victim'
        victim.write_text('original')
        target.symlink_to(victim)
        self.worker.tick(2)
        self.assertEqual(victim.read_text(), 'original')
        self.assertEqual(self.worker.state['sessions'][self.sid]['status'], 'retry')

    def test_missing_source_or_database_does_not_delete_copy(self):
        self.worker.tick(0)
        dest = self.dest()
        self.source.unlink()
        self.worker.tick(2)
        self.worker.tick(30)
        self.assertTrue((dest / m.layout.HISTORY).exists())
        self.db.rename(self.db.with_suffix('.disabled'))
        with self.assertRaises(RuntimeError):
            self.worker.tick(40)
        self.assertTrue((dest / m.layout.HISTORY).exists())

    def test_missing_db_row_during_archive_keeps_copy(self):
        self.worker.tick(0)
        self.source.rename(self.home / 'archived_sessions' / (self.source.stem + '_' + str(uuid.uuid4()) + '.jsonl'))
        with sqlite3.connect(self.db) as c:
            c.execute('DELETE FROM threads WHERE id=?', (self.sid,))
        self.worker.tick(2)
        self.worker.tick(30)
        self.assertTrue((self.dest() / m.layout.HISTORY).exists())

    def test_source_changes_mid_copy_retains_previous_snapshot(self):
        self.worker.tick(0)
        dest = self.dest() / m.layout.HISTORY
        original = dest.read_bytes()
        self.source.write_bytes(original + b'{}\n')
        before = m.signature(self.source)
        with patch.object(m, 'signature', return_value=None):
            with self.assertRaises(RuntimeError):
                self.worker.copy(self.source, dest, before)
        self.assertEqual(dest.read_bytes(), original)
        self.worker.tick(2)
        self.assert_copy()

    def test_restart_recovers_crash_after_whole_directory_rename(self):
        self.worker.tick(0)
        (self.dest() / '工作/result.txt').write_text('recover')
        self.update(name='new-A')
        rename = os.rename
        def move_then_crash(src, dst):
            rename(src, dst)
            raise OSError('simulated crash after rename')
        with patch.object(m.os, 'rename', side_effect=move_then_crash):
            self.worker.tick(2)
        self.worker = m.Mirror(self.home, self.state_dir, grace=10)
        self.worker.tick(4)
        self.assertEqual(self.dest(), self.project / 'new-A')
        self.assertEqual((self.dest() / '工作/result.txt').read_text(), 'recover')
        self.assert_copy()

    def test_legacy_task_mirror_migrates_only_its_history(self):
        old = self.project / 'codex-file/old-task/会话/A'
        old.mkdir(parents=True)
        (old / self.source.name).write_bytes(self.source.read_bytes())
        (old / 'user.txt').write_text('unknown legacy ownership')
        m.atomic_json(old / m.MARKER, {'owner': m.OWNER, 'session_id': self.sid, 'transcript_file': self.source.name})
        self.worker.state['sessions'][self.sid] = {'directory': str(old), 'task_directory': str(old.parent.parent), 'source_path': str(self.source)}
        self.worker.tick(0)
        self.assertEqual(self.dest(), self.project / 'A')
        self.assert_copy()
        self.assertEqual((old / 'user.txt').read_text(), 'unknown legacy ownership')
        self.assertFalse((old / self.source.name).exists())
        self.assertNotIn('task_directory', self.worker.state['sessions'][self.sid])

    def test_legacy_root_mirror_converts_filename_without_data_loss(self):
        old = self.project / 'A'
        old.mkdir()
        (old / self.source.name).write_bytes(self.source.read_bytes())
        m.atomic_json(old / m.MARKER, {'owner': m.OWNER, 'session_id': self.sid, 'transcript_file': self.source.name})
        self.worker.state['sessions'][self.sid] = {'directory': str(old)}
        self.worker.tick(0)
        self.assert_copy()
        self.assertFalse((old / self.source.name).exists())

    def test_project_navigation_preserves_user_prose(self):
        nav = self.project / '项目导航.md'
        nav.write_text('# 自己的说明\n\n用户内容。\n<!-- codex-session-mirror:tasks:start -->旧任务<!-- codex-session-mirror:tasks:end -->\n')
        self.worker.tick(0)
        self.worker.tick(2)
        self.assertIn('用户内容。', nav.read_text())
        self.assertNotIn('旧任务', nav.read_text())
        self.assertEqual(nav.read_text().count(m.layout.NAV_START), 1)
        self.assertNotIn(str(self.project), nav.read_text())

    def test_new_session_inside_workspace_keeps_project_root(self):
        self.worker.tick(0)
        sub = self.dest() / '工作/code'
        sub.mkdir()
        b, bs = self.add('B', project=sub)
        self.worker.tick(2)
        self.assertEqual(self.dest(b), self.project / 'B')
        self.assert_copy(b, bs)

    def test_different_projects_are_independent(self):
        self.worker.tick(0)
        other = self.root / 'other'
        other.mkdir()
        b, bs = self.add('B', project=other)
        self.worker.tick(2)
        self.assertEqual(self.dest(b), other / 'B')
        self.assert_copy(b, bs)

    def test_old_inactive_sessions_wait_until_activity(self):
        b, bs = self.add('old')
        self.worker.tick(0)
        self.assertNotIn(b, self.worker.state['sessions'])
        with bs.open('ab') as f:
            f.write(b'{}\n')
        self.worker.tick(2)
        self.assert_copy(b, bs)

    def test_internal_agents_are_not_user_workspaces(self):
        self.worker.tick(0)
        b, _ = self.add('internal', source='{"subagent":{}}')
        self.worker.tick(2)
        self.assertNotIn(b, self.worker.state['sessions'])

    def test_locate_returns_actual_workspace_after_rename(self):
        self.worker.tick(0)
        self.update(name='new-A')
        self.worker.tick(2)
        r = subprocess.run([sys.executable, m.__file__, '--state-dir', str(self.state_dir), '--locate', '--session-id', self.sid, '--wait', '0'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)['directory'], str(self.project / 'new-A'))

    def test_real_background_process_runs_without_model_and_resumes(self):
        command = [sys.executable, m.__file__, '--codex-home', str(self.home), '--state-dir', str(self.state_dir), '--seed', self.sid, '--interval', '0.5', '--delete-grace', '1']
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        def cleanup():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            process.stderr.close()
        self.addCleanup(cleanup)
        def wait(predicate):
            end = time.monotonic() + 8
            while time.monotonic() < end:
                if predicate(): return
                if process.poll() is not None: self.fail(process.stderr.read().decode())
                time.sleep(.05)
            self.fail('background operation did not converge')
        target = self.project / 'A' / m.layout.HISTORY
        wait(lambda: target.exists() and target.read_bytes() == self.source.read_bytes())
        (self.project / 'A/工作/output.txt').write_text('artifact')
        self.update(name='renamed')
        renamed = self.project / 'renamed'
        wait(lambda: (renamed / '工作/output.txt').exists())
        process.terminate(); process.wait(timeout=5); process.stderr.close()
        with self.source.open('ab') as f: f.write(b'{"during_downtime":true}\n')
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        wait(lambda: (renamed / m.layout.HISTORY).read_bytes() == self.source.read_bytes())
        self.update(archived=1)
        archive = self.project / '已归档/renamed.zip'
        wait(lambda: archive.exists() and not renamed.exists())
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(z.read('renamed/工作/output.txt'), b'artifact')
        self.update(archived=0)
        wait(lambda: (renamed / m.layout.HISTORY).exists() and not archive.exists())
        self.delete()
        wait(lambda: not (renamed / m.layout.HISTORY).exists())
        self.assertEqual((renamed / '工作/output.txt').read_text(), 'artifact')


if __name__ == '__main__':
    unittest.main(verbosity=2)
