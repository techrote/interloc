from __future__ import annotations
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from interloc.capabilities.github.process import ReadError, ProcessResult
from interloc.capabilities.github.worktree import Worktree, inspect_worktree

GIT = shutil.which('git')


@unittest.skipUnless(GIT, 'Git is not installed')
class WorktreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interloc synthetic ')
        self.base = Path(self.temp.name)
        self.root = self.base / 'project space ü'
        self.root.mkdir()
        self.env = dict(os.environ)
        for key in list(self.env):
            if key.startswith(('GIT_', 'GH_', 'GITHUB_')) or key in {'SSH_AUTH_SOCK', 'LD_PRELOAD'}:
                del self.env[key]
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
        self.git('init', '-b', 'main')
        (self.root / 'one.txt').write_text('synthetic\n', encoding='utf-8')
        self.git('add', '--', 'one.txt')
        self.git('-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid', 'commit', '-m', 'synthetic')
        self.tree = Worktree('tree', self.root, self.root/'.git', self.root/'.git')
        self.exe = Path(GIT)

    def tearDown(self):
        # Windows Git marks some object files read-only; fixture cleanup changes
        # only explicitly created disposable files, never enrolled user data.
        def repair(function, path, exc):
            os.chmod(path, 0o700); function(path)
        shutil.rmtree(self.root, onerror=repair)
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run([GIT, '-C', str(self.root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=True, env=self.env, timeout=10).stdout

    def read(self, tree=None, **kw):
        return inspect_worktree(tree or self.tree, self.exe, **kw)

    def snapshot(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file()}

    def assert_code(self, expected, callable_, *args, **kw):
        with self.assertRaises(ReadError) as caught:
            callable_(*args, **kw)
        self.assertEqual(caught.exception.code, expected)

    def test_clean_status_retains_all_files_index_and_head(self):
        before = self.snapshot()
        result = self.read()
        self.assertEqual(result['head_sha'], self.git('rev-parse', 'HEAD').decode().strip())
        self.assertEqual(result['branch'], 'main')
        self.assertFalse(result['dirty'])
        self.assertEqual(result['entries'], [])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root/'.git/index.lock').exists())

    def test_modified_staged_deleted_untracked_unicode_paths(self):
        (self.root/'one.txt').write_text('changed\n')
        (self.root/'new ü.txt').write_text('new\n')
        self.git('add', '--', 'one.txt')
        before = self.snapshot()
        result = self.read()
        states = {r['path']: r['status'] for r in result['entries']}
        self.assertEqual(states['one.txt'], 'M ')
        self.assertEqual(states['new ü.txt'], '??')
        self.assertEqual(self.snapshot(), before)
        (self.root/'one.txt').unlink()
        self.assertIn('D', next(r['status'] for r in self.read()['entries'] if r['path']=='one.txt'))

    def test_malicious_fsmonitor_filter_diff_hooks_and_includes_never_execute(self):
        marker = self.base/'must-not-execute'
        command = 'echo executed > "' + str(marker) + '"'
        # All modifications are to a synthetic repository created by this test.
        self.git('config', 'core.fsmonitor', command)
        self.git('config', 'filter.evil.clean', command)
        self.git('config', 'filter.evil.smudge', command)
        self.git('config', 'diff.evil.command', command)
        self.git('config', 'core.hooksPath', str(self.base/'hostile-hooks'))
        self.git('config', 'include.path', str(self.base/'absent-included-config'))
        (self.root/'.gitattributes').write_text('*.txt filter=evil diff=evil\n')
        (self.root/'one.txt').write_text('different size\n')
        before = self.snapshot()
        result = self.read()
        self.assertTrue(result['dirty'])
        self.assertFalse(marker.exists())
        self.assertEqual(self.snapshot(), before)

    def test_poisoned_git_environment_is_not_inherited(self):
        marker = self.base/'must-not-execute'
        cfg = self.base/'global.ini'
        cfg.write_text('[core]\nfsmonitor = echo bad > "' + str(marker) + '"\n')
        with patch.dict(os.environ, {'GIT_CONFIG_GLOBAL':str(cfg), 'GIT_CONFIG_COUNT':'1', 'GIT_CONFIG_KEY_0':'core.fsmonitor', 'GIT_CONFIG_VALUE_0':'evil', 'GIT_EXEC_PATH':str(self.base/'evil'), 'GIT_INDEX_FILE':str(self.base/'private-index')}):
            result = self.read()
        self.assertFalse(result['dirty'])
        self.assertFalse(marker.exists())
        self.assertFalse((self.base/'private-index').exists())

    def test_detached_and_packed_head(self):
        expected = self.git('rev-parse', 'HEAD').decode().strip()
        self.git('pack-refs', '--all')
        self.assertEqual(self.read()['head_sha'], expected)
        self.git('checkout', '--detach')
        result = self.read()
        self.assertIsNone(result['branch'])
        self.assertEqual(result['head_sha'], expected)

    def test_unborn_repository_and_absent_index(self):
        other = self.base/'unborn'; other.mkdir()
        subprocess.run([GIT,'init','-b','main',str(other)], env=self.env, capture_output=True, check=True)
        result = self.read(Worktree('unborn', other, other/'.git', other/'.git'))
        self.assertTrue(result['unborn'])
        self.assertFalse(result['dirty'])
        self.assertIsNone(result['index_sha256'])

    def test_linked_worktree_uses_independently_enrolled_common_dir(self):
        linked = self.base/'linked space'
        self.git('worktree', 'add', '-b', 'other', str(linked))
        git_dir = Path((linked/'.git').read_text(encoding='utf-8').strip()[8:])
        tree = Worktree('linked', linked, git_dir, self.root/'.git')
        result = self.read(tree)
        self.assertEqual(result['branch'], 'other')
        self.assertFalse(result['dirty'])
        wrong = Worktree('linked', linked, git_dir, self.base)
        self.assert_code('WORKTREE_IDENTITY_CHANGED', self.read, wrong)

    def test_pointer_to_unenrolled_git_directory_is_denied(self):
        other = self.base/'other'; other.mkdir()
        (other/'.git').write_text('gitdir: ' + str(self.root/'.git'))
        wrong = Worktree('other', other, other/'not-enrolled', self.root/'.git')
        with self.assertRaises(ReadError):
            self.read(wrong)

    def test_split_index_is_explicitly_unsupported_not_silently_reset(self):
        self.git('update-index', '--split-index')
        before = self.snapshot()
        self.assert_code('GIT_STATUS_UNAVAILABLE', self.read)
        self.assertEqual(self.snapshot(), before)

    def test_malformed_index_is_refused(self):
        (self.root/'.git/index').write_bytes(b'DIRCmalformed')
        self.assert_code('GIT_FORMAT_UNSUPPORTED', self.read)

    def test_external_alternate_object_store_is_refused(self):
        (self.root/'.git/objects/info/alternates').write_text(str(self.base/'outside')+'\n')
        self.assert_code('GIT_FORMAT_UNSUPPORTED', self.read)

    def test_target_index_change_during_observation_discards_output(self):
        def change(argv, **kw):
            (self.root/'.git/index').write_bytes(b'changed')
            return ProcessResult(0,b'',b'')
        self.assert_code('WORKTREE_CHANGED', self.read, runner=change)

    def test_target_head_change_during_observation_discards_output(self):
        def change(argv, **kw):
            (self.root/'.git/HEAD').write_text('b'*40+'\n')
            return ProcessResult(0,b'',b'')
        self.assert_code('WORKTREE_CHANGED', self.read, runner=change)

    def test_target_working_file_change_during_observation_discards_output(self):
        def change(argv, **kw):
            (self.root/'one.txt').write_text('changed while observing')
            return ProcessResult(0,b'',b'')
        self.assert_code('WORKTREE_CHANGED', self.read, runner=change)

    def test_malformed_and_overlong_status_output_is_not_success(self):
        for output in (b'not-terminated',b' M ../escape\0',b' M /absolute\0',b' M C:stream\0',b'@@ broken\0',b'?? new\0'*1001):
            with self.subTest(output=output[:20]):
                def runner(argv, **kw):
                    return ProcessResult(0,output,b'')
                with self.assertRaises(ReadError):
                    self.read(runner=runner)

    def test_symlink_and_dangling_index_cannot_escape(self):
        target = self.base/'outside'; target.write_text('synthetic outside data')
        link = self.root/'link'
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest('symlinks require Windows developer mode or privilege')
        self.assert_code('PATH_DENIED', self.read)
        link.unlink()
        index = self.root/'.git/index'; index.unlink(); index.symlink_to(self.base/'absent')
        self.assert_code('PATH_DENIED', self.read)
        index.unlink()

    @unittest.skipUnless(os.name == 'nt', 'Windows junction test')
    def test_windows_junction_cannot_escape(self):
        target = self.base/'outside-dir'; target.mkdir()
        link = self.root/'junction'
        subprocess.run(['cmd.exe','/c','mklink','/J',str(link),str(target)], capture_output=True, check=True)
        try:
            self.assertTrue(link.is_junction())
            self.assert_code('PATH_DENIED', self.read)
        finally:
            link.rmdir()

    @unittest.skipIf(os.name == 'nt', 'POSIX FIFO test')
    def test_fifo_is_rejected_without_blocking_open(self):
        fifo = self.root/'pipe'; os.mkfifo(fifo)
        self.assert_code('PATH_DENIED', self.read)
        fifo.unlink()

    def test_precancel_and_worktree_size_limit(self):
        self.assert_code('CANCELLED', self.read, cancelled=lambda: True)
        with (self.root/'large').open('wb') as f:
            f.truncate(257 * 1024 * 1024)
        self.assert_code('WORKTREE_LIMIT', self.read)

    def test_raw_filter_semantics_are_explicit(self):
        result = self.read()
        self.assertEqual(result['configuration'], 'isolated-no-filters')
        self.assertEqual(result['submodules'], 'not_inspected')


if __name__ == '__main__':
    unittest.main()
