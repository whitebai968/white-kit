import contextlib
import io
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import apply_rules
import install


class RuleDistributionTests(unittest.TestCase):
    def test_merge_preserves_other_top_level_sections(self):
        before = '# 协作\n保持沟通规则\n\n# 文件与产物管理\n旧目录规则\n\n# 其他配置\n保留这一段\n'
        after = apply_rules.merge_rules(before, '# 文件与产物管理\n新的目录规则', True)
        self.assertTrue(after.startswith('# 协作\n保持沟通规则\n\n'))
        self.assertIn('# 其他配置\n保留这一段', after)
        self.assertNotIn('旧目录规则', after)

    def test_ambiguous_existing_rules_require_explicit_replacement(self):
        with self.assertRaises(ValueError):
            apply_rules.merge_rules('# 文件与产物管理\n旧规则', '# 文件与产物管理\n新规则')

    def test_managed_rules_are_idempotent(self):
        fragment = '# 文件与产物管理\n规则'
        once = apply_rules.merge_rules('# 用户说明\n保留', fragment)
        self.assertEqual(apply_rules.merge_rules(once, fragment), once)

    def test_override_preview_then_apply_with_backup_in_fake_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            profile = root / '另一台 Mac 的 Codex'
            profile.mkdir()
            base = profile / 'AGENTS.md'
            override = profile / 'AGENTS.override.md'
            base.write_text('# Base\nunchanged\n')
            override.write_text('# Local preferences\nkeep this text\n')
            before = override.read_bytes()
            arguments = ['apply_rules.py', '--codex-home', str(profile)]
            with patch.object(sys, 'argv', arguments), contextlib.redirect_stdout(io.StringIO()):
                apply_rules.main()
            self.assertEqual(override.read_bytes(), before)
            with patch.object(sys, 'argv', arguments + ['--apply', '--backup-dir', str(root / 'backups')]), contextlib.redirect_stdout(io.StringIO()):
                apply_rules.main()
            self.assertTrue(override.read_text().startswith(before.decode()))
            self.assertIn(apply_rules.START, override.read_text())
            self.assertEqual(base.read_text(), '# Base\nunchanged\n')
            backups = list((root / 'backups').rglob('AGENTS.override.md'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), before)

    def test_existing_file_requires_backup_for_apply(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            target = root / 'AGENTS.md'
            target.write_text('keep')
            with patch.object(sys, 'argv', ['apply_rules.py', '--codex-home', str(root), '--apply']):
                with self.assertRaises(SystemExit):
                    apply_rules.main()
            self.assertEqual(target.read_text(), 'keep')


class MacPortabilityTests(unittest.TestCase):
    def profile(self, root):
        profile = root / '自定义 Codex profile'
        (profile / 'sessions').mkdir(parents=True)
        sid = '11111111-2222-3333-4444-555555555555'
        transcript = profile / 'sessions' / ('rollout-test-' + sid + '.jsonl')
        transcript.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': sid}}) + '\n')
        with sqlite3.connect(profile / 'state_5.sqlite') as c:
            c.execute('CREATE TABLE threads(id TEXT, title TEXT, cwd TEXT, rollout_path TEXT, archived INTEGER)')
            c.execute('INSERT INTO threads VALUES(?,?,?,?,?)', (sid, 'test', str(root), str(transcript), 0))
        return profile, sid

    def test_installer_uses_target_home_profile_python_and_uid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            target_home = root / 'Other Mac User'
            target_home.mkdir()
            profile, sid = self.profile(root)
            ok = subprocess.CompletedProcess([], 0, '', '')
            with patch.object(Path, 'home', return_value=target_home), patch.object(install.os, 'getuid', return_value=777):
                with patch.object(install.subprocess, 'run', return_value=ok) as run:
                    with patch.object(sys, 'argv', ['install.py', 'install', '--codex-home', str(profile), '--seed', sid]), contextlib.redirect_stdout(io.StringIO()):
                        install.main()
            plist = target_home / 'Library/LaunchAgents/local.codex.session-mirror.plist'
            with plist.open('rb') as f:
                job = plistlib.load(f)
            argv = job['ProgramArguments']
            self.assertEqual(argv[0], sys.executable)
            self.assertEqual(argv[argv.index('--codex-home') + 1], str(profile))
            self.assertIn(str(target_home / 'Library/Application Support/CodexSessionMirror/session_mirror.py'), argv)
            self.assertTrue(any(call.args[0][:3] == ['launchctl', 'bootstrap', 'gui/777'] for call in run.call_args_list))
            self.assertIn(sid, argv)
            installed_fork = target_home / 'Library/Application Support/CodexSessionMirror/fork_workspace.py'
            self.assertEqual(installed_fork.read_bytes(), (Path(install.__file__).parent / 'fork_workspace.py').read_bytes())
            installed_archive = target_home / 'Library/Application Support/CodexSessionMirror/archive_store.py'
            self.assertEqual(installed_archive.read_bytes(), (Path(install.__file__).parent / 'archive_store.py').read_bytes())

    def test_missing_seed_fails_before_installing_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            home = root / 'home'
            home.mkdir()
            profile, _ = self.profile(root)
            with patch.object(Path, 'home', return_value=home):
                with patch.object(sys, 'argv', ['install.py', 'install', '--codex-home', str(profile), '--seed', 'not-in-this-profile']):
                    with self.assertRaises(SystemExit):
                        install.main()
            self.assertFalse((home / 'Library').exists())

    def test_codex_home_environment_is_resolved_at_install(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            home = root / 'home'
            home.mkdir()
            profile, sid = self.profile(root)
            ok = subprocess.CompletedProcess([], 0, '', '')
            with patch.object(Path, 'home', return_value=home), patch.dict(os.environ, {'CODEX_HOME': str(profile)}):
                with patch.object(install.subprocess, 'run', return_value=ok), patch.object(sys, 'argv', ['install.py', 'install', '--seed', sid]), contextlib.redirect_stdout(io.StringIO()):
                    install.main()
            with (home / 'Library/LaunchAgents/local.codex.session-mirror.plist').open('rb') as f:
                argv = plistlib.load(f)['ProgramArguments']
            self.assertEqual(argv[argv.index('--codex-home') + 1], str(profile))


if __name__ == '__main__':
    unittest.main()
