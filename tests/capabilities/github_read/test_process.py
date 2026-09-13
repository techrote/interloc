from __future__ import annotations
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from interloc.capabilities.github.process import ReadError, environment, run_bounded


class ProcessTests(unittest.TestCase):
    def run_child(self, code, **kwargs):
        with tempfile.TemporaryDirectory() as temp:
            return run_bounded([sys.executable, '-I', '-c', code], cwd=Path(temp), env=environment(), **kwargs)

    def test_fragmented_unicode_and_no_newline_are_exact(self):
        result = self.run_child("import os; [os.write(1,bytes([b])) for b in 'h\\u00e9llo'.encode()]; os.write(2,b'warn')")
        self.assertEqual(result.stdout, 'héllo'.encode())
        self.assertEqual(result.stderr, b'warn')
        self.assertEqual(result.returncode, 0)

    def test_nonzero_exit_is_not_silence_success(self):
        result = self.run_child('raise SystemExit(7)')
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b'')

    def test_stdout_and_stderr_floods_are_rejected_during_capture(self):
        for fd in (1, 2):
            before = time.monotonic()
            with self.assertRaises(ReadError) as caught:
                self.run_child(f'import os\nwhile True: os.write({fd},b"x"*4096)', max_stdout=8192, max_stderr=8192, timeout=5)
            self.assertEqual(caught.exception.code, 'OUTPUT_LIMIT')
            self.assertLess(time.monotonic() - before, 5)

    def test_timeout_and_cancellation_clean_up(self):
        with self.assertRaises(ReadError) as caught:
            self.run_child('import time; time.sleep(10)', timeout=.2)
        self.assertEqual(caught.exception.code, 'PROCESS_TIMEOUT')
        cancel = threading.Event()
        timer = threading.Timer(.2, cancel.set); timer.start()
        try:
            with self.assertRaises(ReadError) as caught:
                self.run_child('import time; time.sleep(10)', cancelled=cancel.is_set)
            self.assertEqual(caught.exception.code, 'CANCELLED')
        finally:
            timer.join()

    def test_pre_cancel_never_spawns(self):
        with patch('subprocess.Popen') as popen:
            with self.assertRaises(ReadError):
                self.run_child('pass', cancelled=lambda: True)
            popen.assert_not_called()

    def test_descendant_holding_pipe_dies_before_late_side_effect(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'must-not-appear'
            child = f'import time; from pathlib import Path; time.sleep(1.5); Path({str(marker)!r}).write_text("bad")'
            parent = f'import subprocess,sys; subprocess.Popen([sys.executable,"-I","-c",{child!r}])'
            with self.assertRaises(ReadError) as caught:
                self.run_child(parent, timeout=.4)
            self.assertEqual(caught.exception.code, 'PROCESS_TIMEOUT')
            time.sleep(1.6)
            self.assertFalse(marker.exists())

    def test_no_credentials_or_python_git_injection_environment(self):
        with patch.dict(os.environ, {'GH_TOKEN':'synthetic','GITHUB_TOKEN':'synthetic','SSH_AUTH_SOCK':'synthetic','PYTHONPATH':'synthetic','LD_PRELOAD':'synthetic','GIT_CONFIG_COUNT':'1'}):
            result = self.run_child('import os,json;print(json.dumps(dict(os.environ)))')
        self.assertNotIn(b'synthetic', result.stdout)

    def test_relative_or_batch_executable_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            for exe in ('git', str(Path(temp)/'unsafe.cmd'), str(Path(temp)/'unsafe.bat')):
                with self.assertRaises(ReadError) as caught:
                    run_bounded([exe], cwd=Path(temp), env={})
                self.assertEqual(caught.exception.code, 'EXECUTABLE_INVALID')

    def test_absent_executable_is_typed(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ReadError) as caught:
                run_bounded([str(Path(temp)/'absent.exe')], cwd=Path(temp), env=environment())
            self.assertEqual(caught.exception.code, 'EXECUTABLE_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
