"""Regressions for workspace spellings; native containment is tested separately."""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

# Normal project tests already configure src; permit this exact-source subset too.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))
from omni_body_skill.tools import sandbox_runtime as sandbox


class WorkspaceAliasTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.real = self.root / 'canonical user' / '工作 区'
        self.real.mkdir(parents=True)
        self.alias = self.root / 'SHORT~1' / '工作 区'
        self.target = self.root / 'private' / 'workspace'
        self.original_resolve = Path.resolve
        self.alias_target = self.real
        owner = self

        def observe(path, *args, **kwargs):
            try:
                relative = path.relative_to(owner.alias)
            except ValueError:
                return owner.original_resolve(path, *args, **kwargs)
            return owner.original_resolve(owner.alias_target / relative, *args, **kwargs)

        self.patch = mock.patch.object(Path, 'resolve', observe)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def rewrite(self, command):
        return sandbox._rewrite_workspace_paths(command, self.alias, self.target)

    def test_embedded_original_spelling_is_rewritten(self):
        command = f'cd "{self.alias / "project"}" && echo done'
        self.assertEqual(self.rewrite(command), f'cd "{self.target / "project"}" && echo done')

    def test_original_and_canonical_spellings_in_same_command(self):
        command = f'copy "{self.alias / "in.txt"}" "{self.real / "out.txt"}"'
        rewritten = self.rewrite(command)
        self.assertNotIn(str(self.alias), rewritten)
        self.assertNotIn(str(self.real), rewritten)
        self.assertEqual(rewritten.count(str(self.target)), 2)

    def test_list_shape_and_non_path_arguments_are_retained(self):
        command = ['cmd.exe', '/d', '/s', '/c', f'cd /d "{self.alias}" && echo ok']
        result = self.rewrite(command)
        self.assertEqual(result[:-1], command[:-1])
        self.assertEqual(result[-1], f'cd /d "{self.target}" && echo ok')

    def test_standalone_alias_descendant_is_rewritten(self):
        result = self.rewrite([str(self.alias / 'project' / 'x.txt')])
        self.assertEqual(result, [str(self.target / 'project' / 'x.txt')])

    def test_sibling_prefix_is_not_rewritten(self):
        command = f'echo "{self.alias}_other/x" "{self.real}_other/x"'
        self.assertEqual(self.rewrite(command), command)

    def test_replacement_is_not_recursively_rewritten(self):
        nested_target = self.alias / 'private'
        command = f'echo "{self.alias / "a"}" "{self.real / "b"}"'
        result = sandbox._rewrite_workspace_paths(command, self.alias, nested_target)
        expected = f'echo "{self.real / "private" / "a"}" "{self.real / "private" / "b"}"'
        self.assertEqual(result, expected)

    def test_native_separator_variant(self):
        path = str(self.alias / 'file.txt')
        if os.name == 'nt':
            path = path.replace('\\', '/').swapcase()
        result = self.rewrite(f'cat "{path}"')
        self.assertNotIn('SHORT~1', result.upper())
        self.assertIn(str(self.target), result)

    def test_runner_keeps_supplied_alias_until_rewrite(self):
        runner = sandbox.SandboxRunner(self.alias, self.root / 'state', self.root / 'trash')
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(sandbox, '_copy_workspace'))
            stack.enter_context(mock.patch.object(sandbox, '_snapshot', return_value={}))
            stack.enter_context(mock.patch.object(sandbox, '_merge_changes', return_value={}))
            stack.enter_context(mock.patch.object(sandbox, '_prepare_windows_utf8_shell_command', side_effect=lambda cmd, **kw: cmd))
            calls = []

            def observe_run(command, *args, **kwargs):
                calls.append(command)
                return 0, b'', b'', 'test-observer-not-containment'

            stack.enter_context(mock.patch.object(sandbox, '_run_portable', side_effect=observe_run))
            stack.enter_context(mock.patch.object(sandbox, '_run_windows_appcontainer', side_effect=observe_run))
            # Use existing explicit compatibility only for a Windows fixture without
            # storage APIs. This test makes no native isolation claim.
            stack.enter_context(mock.patch.dict(os.environ, {'TIANGONG_SANDBOX_COMPAT': '1'}))
            runner.run(f'echo "{self.alias / "x"}"', op_id='alias-rewrite-fixture')
        self.assertEqual(len(calls), 1)
        self.assertNotIn(str(self.alias), calls[0])
        self.assertNotIn(str(self.real), calls[0])

    def test_changed_alias_identity_is_rejected_before_copy_or_launch(self):
        runner = sandbox.SandboxRunner(self.alias, self.root / 'state', self.root / 'trash')
        self.alias_target = self.root / 'different-workspace'
        self.alias_target.mkdir()
        with mock.patch.object(sandbox, '_copy_workspace') as copy:
            with self.assertRaisesRegex(sandbox.SandboxError, 'sandbox_workspace_identity_changed'):
                runner.run(['unused'], op_id='identity-drift-fixture')
            copy.assert_not_called()

    def test_private_target_does_not_change_containment_requirement(self):
        runner = sandbox.SandboxRunner(self.alias, self.root / 'state', self.root / 'trash')
        with self.assertRaises(sandbox.SandboxError):
            runner.run(['unused'], require_os_containment='yes')


if __name__ == '__main__':
    unittest.main()
