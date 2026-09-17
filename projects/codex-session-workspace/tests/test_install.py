from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import install


class LaunchAgentTests(unittest.TestCase):
    def test_transient_launchd_reload_error_is_retried(self):
        failure = subprocess.CompletedProcess([], 5, '', 'Bootstrap failed: 5: Input/output error')
        success = subprocess.CompletedProcess([], 0, '', '')
        with patch.object(install.subprocess, 'run', side_effect=[failure, success]) as run:
            with patch.object(install.time, 'sleep'):
                install.bootstrap('gui/501', Path('/tmp/test.plist'))
        self.assertEqual(run.call_count, 2)

    def test_persistent_failure_is_reported_after_bounded_retries(self):
        failure = subprocess.CompletedProcess([], 5, '', 'Permission denied')
        with patch.object(install.subprocess, 'run', return_value=failure) as run:
            with patch.object(install.time, 'sleep'):
                with self.assertRaisesRegex(RuntimeError, 'Permission denied'):
                    install.bootstrap('gui/501', Path('/tmp/test.plist'))
        self.assertEqual(run.call_count, 5)


if __name__ == '__main__':
    unittest.main()
